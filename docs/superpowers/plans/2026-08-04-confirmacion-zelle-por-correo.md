# Confirmación de Zelle por correo — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Al reenviar una captura de Zelle al grupo, verificar contra los buzones de correo de las cuentas alquiladas si el banco notificó un pago del mismo monto, y avisarle al operador por WhatsApp (confirmado, o escalera de recordatorios hasta cerrar a la hora).

**Architecture:** Un poller de Celery (cada 60 s) lee por IMAP las cabeceras de los buzones configurados, parsea el asunto (nombre + monto) y guarda cada notificación en `bank_email_notifications`. Cuando el bot reenvía una captura al grupo, llama a `POST /whatsapp/payments/incoming/{id}/verify-by-email`; el backend busca en esa tabla y, si no hay match todavía, deja una fila `bank_email_verifications` que el poller reevalúa cada vuelta, avisando de forma escalonada. El bot no toca IMAP.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery + Redis, `imaplib` (librería estándar), aiohttp para el aviso al bot, pytest. Lado bot: TypeScript (whatsapp-web.js).

## Global Constraints

- Spec de referencia: `backend/docs/superpowers/specs/2026-08-04-confirmacion-zelle-por-correo-design.md`. Ante cualquier duda de comportamiento, manda el spec.
- **Los mensajes de commit van en inglés** (`backend/CLAUDE.md`). Los comentarios y docstrings del código, en español, como el resto del backend.
- Rama de trabajo: `feat/zelle-email-confirmation` (ya existe en el repo `backend/`, con el spec commiteado).
- Todos los `datetime` son *timezone-aware* en UTC. Nunca `datetime.now()` sin tz; siempre `datetime.now(timezone.utc)`.
- Los montos de dinero se manejan con `Decimal`, nunca `float`, salvo al leer `WhatsAppIncomingPayment.amount` (que es `Float` en la BD) — ahí se convierte con `Decimal(str(valor))`.
- Toda función que dependa del reloj recibe `now` como parámetro. Nada de llamar al reloj dentro de la lógica: rompe los tests.
- No se agregan dependencias nuevas a `requirements.txt`. `imaplib`, `email` y `re` son de la librería estándar; `redis==5.0.1` y `aiohttp==3.11.10` ya están.
- Los tests que necesitan Postgres usan las fixtures de `tests/conftest.py` (se saltan solos si no hay Postgres en :5433). Los tests de lógica pura NO usan BD.
- Ninguna pantalla nueva en el frontend. Este plan no toca `frontend/`.

---

## Estructura de archivos

**Backend — nuevos**

| Archivo | Responsabilidad |
|---|---|
| `app/services/bank_email_parsers.py` | Plantillas por banco, validación de autenticidad y parseo del asunto. Puro: sin BD, sin red. |
| `app/services/bank_email_matching.py` | Elegir qué notificación confirma qué pago, y la escalera de avisos. Puro: sin BD, sin red. |
| `app/services/bank_email_imap.py` | Único lugar que habla IMAP. Devuelve `RawEmailHeaders`. |
| `app/services/bank_email_service.py` | Orquesta: ingesta → resolución → escalada, contra la BD. |
| `app/services/bot_notifier.py` | Mandar texto al operador vía el bot (`BOT_NOTIFY_URL`). |
| `app/models/bank_email.py` | `BankEmailNotification`, `BankEmailVerification`. |
| `app/tasks/bank_email_tasks.py` | Tarea Celery `poll_bank_emails` + lock en Redis. |
| `app/cli/check_mailboxes.py` | Diagnóstico manual contra Gmail. |
| `alembic/versions/<rev>_add_bank_email_tables.py` | Migración. |

**Backend — modificados**

| Archivo | Cambio |
|---|---|
| `app/core/config.py` | `ZELLE_MAILBOXES` + helper que lo parsea |
| `app/models/__init__.py` | Registrar los modelos nuevos |
| `app/celery_app.py` | `include` + `beat_schedule` |
| `app/routers/whatsapp.py` | `POST /payments/incoming/{id}/verify-by-email` |
| `app/schemas/whatsapp.py` | Response del endpoint |
| `app/services/alert_service.py` | `_post_to_bot` delega en `bot_notifier` (borra duplicación) |

**Bot — modificados**

| Archivo | Cambio |
|---|---|
| `src/api-client.ts` | `apiVerifyIncomingByEmail` |
| `src/op-bridge.ts` | `bridgeVerifyIncomingByEmail` |
| `src/whatsapp.ts` | Llamada en la rama `forwardedSource` + línea en el mensaje al operador |

**Por qué separado así:** `bank_email_parsers` y `bank_email_matching` son las dos piezas donde vive toda la lógica que puede equivocarse, y ninguna toca BD ni red — se prueban en milisegundos y sin Postgres. `bank_email_imap` aísla lo único que no se puede testear sin Gmail, detrás de una interfaz de una función. `bank_email_service` es pegamento.

---

## Task 1: Parser de asuntos y autenticidad

**Files:**
- Create: `app/services/bank_email_parsers.py`
- Test: `tests/test_bank_email_parsers.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `@dataclass RawEmailHeaders(message_id: str, subject: str, from_addr: str, to_addr: str, received_at: datetime, auth_results: str)`
  - `@dataclass BankTemplate(bank: str, from_address: str, auth_domain: str, subject_regex: str)`
  - `@dataclass ParsedBankEmail(message_id, mailbox_label, mailbox_email, bank, sender_name, amount: Decimal, currency: str, received_at: datetime, subject: str, auth_result: str)`
  - `TEMPLATES: list[BankTemplate]`
  - `find_template(from_addr: str) -> Optional[BankTemplate]`
  - `is_forwarded(subject: str) -> bool`
  - `authentication_ok(auth_results: str, auth_domain: str) -> bool`
  - `parse_bank_email(raw: RawEmailHeaders, mailbox_label: str) -> Optional[ParsedBankEmail]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_parsers.py`:

```python
"""
Parseo y autenticidad de los correos de notificación de los bancos
(app/services/bank_email_parsers.py).

Puro, sin BD ni red: corre en cualquier lado.

Los asuntos son reales, tomados de los buzones de las cuentas alquiladas. Si un banco
cambia el formato del asunto, el caso nuevo se agrega AQUÍ antes de tocar el regex.
"""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.services.bank_email_parsers import (
    RawEmailHeaders,
    authentication_ok,
    find_template,
    is_forwarded,
    parse_bank_email,
)

NOW = datetime(2026, 8, 4, 18, 1, tzinfo=timezone.utc)

BOA_AUTH = "mx.google.com; dkim=pass header.i=@bankofamerica.com; spf=pass; dmarc=pass"
FNBT_AUTH = "mx.google.com; dkim=neutral; spf=pass smtp.mailfrom=1stnb.com; dmarc=pass"


def raw(**kw) -> RawEmailHeaders:
    base = dict(
        message_id="<abc@bankofamerica.com>",
        subject="Carlos R Barrientos le envió $30.00",
        from_addr="customerservice@ealerts.bankofamerica.com",
        to_addr="azocarjean98@gmail.com",
        received_at=NOW,
        auth_results=BOA_AUTH,
    )
    base.update(kw)
    return RawEmailHeaders(**base)


# ---------- find_template ----------

def test_encuentra_plantilla_de_bank_of_america():
    t = find_template("customerservice@ealerts.bankofamerica.com")
    assert t is not None and t.bank == "BANK_OF_AMERICA"


def test_encuentra_plantilla_ignorando_mayusculas():
    # FNBT manda desde "CustServ@1stnb.com"; el From llega con capitalización variable.
    t = find_template("custserv@1stnb.com")
    assert t is not None and t.bank == "FNBT"


def test_remitente_parecido_pero_falso_no_tiene_plantilla():
    # Guion en vez de punto: dominio distinto, controlado por el atacante.
    assert find_template("customerservice@ealerts-bankofamerica.com") is None


def test_remitente_desconocido_no_tiene_plantilla():
    assert find_template("noreply@paypal.com") is None


# ---------- is_forwarded ----------

@pytest.mark.parametrize("subject", [
    "Fwd: Carlos R Barrientos le envió $30.00",
    "RV: Carlos R Barrientos le envió $30.00",
    "Fw: Carlos R Barrientos le envió $30.00",
    "fwd: carlos le envió $30.00",
])
def test_detecta_reenviados(subject):
    assert is_forwarded(subject) is True


def test_asunto_normal_no_es_reenviado():
    assert is_forwarded("Carlos R Barrientos le envió $30.00") is False


# ---------- authentication_ok ----------

def test_acepta_dkim_pass_del_dominio_esperado():
    assert authentication_ok(BOA_AUTH, "bankofamerica.com") is True


def test_acepta_spf_pass_cuando_dkim_no_pasa():
    # FNBT-FCB puede no tener DKIM alineado; con SPF del dominio correcto alcanza.
    assert authentication_ok(FNBT_AUTH, "1stnb.com") is True


def test_rechaza_dkim_pass_de_otro_dominio():
    fake = "mx.google.com; dkim=pass header.i=@atacante.com; spf=pass smtp.mailfrom=atacante.com"
    assert authentication_ok(fake, "bankofamerica.com") is False


def test_rechaza_cuando_todo_falla():
    fail = "mx.google.com; dkim=fail; spf=softfail; dmarc=fail"
    assert authentication_ok(fail, "bankofamerica.com") is False


def test_rechaza_cabecera_vacia():
    assert authentication_ok("", "bankofamerica.com") is False


# ---------- parse_bank_email ----------

def test_parsea_boa_en_espanol():
    parsed = parse_bank_email(raw(), mailbox_label="Jean")
    assert parsed is not None
    assert parsed.bank == "BANK_OF_AMERICA"
    assert parsed.sender_name == "Carlos R Barrientos"
    assert parsed.amount == Decimal("30.00")
    assert parsed.currency == "USD"
    assert parsed.mailbox_label == "Jean"
    assert parsed.mailbox_email == "azocarjean98@gmail.com"


def test_parsea_boa_en_ingles():
    parsed = parse_bank_email(raw(subject="Carlos R Barrientos sent you $30.00"), mailbox_label="Jean")
    assert parsed is not None and parsed.amount == Decimal("30.00")


def test_parsea_fnbt():
    parsed = parse_bank_email(
        raw(
            subject="Notification - Aristides Bravo sent you $107.00.",
            from_addr="CustServ@1stnb.com",
            to_addr="mmendozaperez53@gmail.com",
            auth_results=FNBT_AUTH,
        ),
        mailbox_label="Mariana",
    )
    assert parsed is not None
    assert parsed.bank == "FNBT"
    assert parsed.sender_name == "Aristides Bravo"
    assert parsed.amount == Decimal("107.00")


def test_parsea_monto_con_separador_de_miles():
    parsed = parse_bank_email(raw(subject="Carlos R Barrientos le envió $1,970.00"), mailbox_label="Jean")
    assert parsed is not None and parsed.amount == Decimal("1970.00")


def test_no_parsea_reenviado():
    assert parse_bank_email(raw(subject="Fwd: Carlos R Barrientos le envió $30.00"), mailbox_label="Jean") is None


def test_no_parsea_remitente_sin_plantilla():
    assert parse_bank_email(raw(from_addr="noreply@paypal.com"), mailbox_label="Jean") is None


def test_no_parsea_asunto_que_no_es_pago():
    # Los bancos mandan mucho correo que no es una notificación de Zelle.
    assert parse_bank_email(raw(subject="Your monthly statement is ready"), mailbox_label="Jean") is None


def test_parse_no_valida_autenticidad():
    # La autenticidad se chequea aparte (authentication_ok) para que el servicio de
    # ingesta pueda avisar "descartado por autenticación" en vez de callar.
    parsed = parse_bank_email(raw(auth_results="dkim=fail; spf=fail"), mailbox_label="Jean")
    assert parsed is not None
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_parsers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bank_email_parsers'`

- [ ] **Step 3: Implementar**

Crear `app/services/bank_email_parsers.py`:

```python
"""
Parseo de las notificaciones de pago que mandan los bancos de las cuentas alquiladas.

Todo sale del ASUNTO y de las cabeceras: los dos bancos ponen nombre y monto ahí
("Carlos R Barrientos le envió $30.00"), y el cuerpo HTML de estos correos cambia sin
aviso. Menos superficie, menos roturas.

Agregar un banco = agregar un `BankTemplate` a TEMPLATES. Nada más.

Puro: sin BD, sin red. Lo que se pueda romper se prueba en tests/test_bank_email_parsers.py.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass
class RawEmailHeaders:
    """Lo que devuelve la capa IMAP: cabeceras crudas, sin interpretar."""

    message_id: str
    subject: str
    from_addr: str
    to_addr: str
    received_at: datetime
    auth_results: str


@dataclass
class BankTemplate:
    #: Identificador interno del banco (se guarda en la columna `bank`).
    bank: str
    #: Dirección exacta desde la que escribe el banco. Es la lista blanca.
    from_address: str
    #: Dominio que tiene que haber pasado DKIM o SPF para creerle al correo.
    auth_domain: str
    #: Debe capturar los grupos `name` y `amount`.
    subject_regex: str


@dataclass
class ParsedBankEmail:
    message_id: str
    mailbox_label: str
    mailbox_email: str
    bank: str
    sender_name: str
    amount: Decimal
    currency: str
    received_at: datetime
    subject: str
    auth_result: str


TEMPLATES: list[BankTemplate] = [
    BankTemplate(
        bank="BANK_OF_AMERICA",
        from_address="customerservice@ealerts.bankofamerica.com",
        auth_domain="bankofamerica.com",
        # El mismo buzón recibe el aviso en español o inglés según la config de la cuenta.
        subject_regex=r"^(?P<name>.+?)\s+(?:le envió|sent you)\s+\$(?P<amount>[\d,]+\.\d{2})",
    ),
    BankTemplate(
        bank="FNBT",
        from_address="custserv@1stnb.com",
        auth_domain="1stnb.com",
        subject_regex=r"^Notification\s*-\s*(?P<name>.+?)\s+sent you\s+\$(?P<amount>[\d,]+\.\d{2})",
    ),
]

#: Prefijos que delatan que alguien reenvió el correo en vez de que lo escribiera el banco.
_FORWARD_PREFIXES = ("fwd:", "fw:", "rv:")


def find_template(from_addr: str) -> Optional[BankTemplate]:
    """Plantilla del banco por dirección exacta del remitente (case-insensitive)."""
    addr = (from_addr or "").strip().lower()
    for template in TEMPLATES:
        if addr == template.from_address.lower():
            return template
    return None


def is_forwarded(subject: str) -> bool:
    return (subject or "").strip().lower().startswith(_FORWARD_PREFIXES)


def authentication_ok(auth_results: str, auth_domain: str) -> bool:
    """
    ¿Gmail verificó que este correo salió del banco?

    Se acepta DKIM o SPF, no se exigen los dos: el banco chico (FNBT-FCB) puede no tener
    DKIM alineado, y exigirlo dejaría esa cuenta sin confirmar EN SILENCIO, que es la
    peor falla posible aquí. En ambos casos el dominio que pasó tiene que ser el del banco.
    """
    text = (auth_results or "").lower()
    domain = (auth_domain or "").lower()
    if not text or not domain:
        return False

    dkim = re.search(r"dkim=pass[^;]*header\.i=@([\w.-]+)", text)
    if dkim and dkim.group(1).endswith(domain):
        return True

    spf = re.search(r"spf=pass[^;]*smtp\.mailfrom=([\w.@-]+)", text)
    if spf and spf.group(1).split("@")[-1].endswith(domain):
        return True

    return False


def parse_bank_email(raw: RawEmailHeaders, mailbox_label: str) -> Optional[ParsedBankEmail]:
    """
    Convierte cabeceras crudas en una notificación de pago, o None si no lo es.

    NO valida autenticidad a propósito: eso lo decide el llamador con `authentication_ok`,
    para poder avisar "descartado por autenticación" en vez de tragárselo.
    """
    template = find_template(raw.from_addr)
    if template is None:
        return None
    if is_forwarded(raw.subject):
        return None

    match = re.match(template.subject_regex, (raw.subject or "").strip())
    if match is None:
        return None

    amount = Decimal(match.group("amount").replace(",", ""))
    return ParsedBankEmail(
        message_id=raw.message_id,
        mailbox_label=mailbox_label,
        mailbox_email=raw.to_addr,
        bank=template.bank,
        sender_name=match.group("name").strip(),
        amount=amount,
        currency="USD",
        received_at=raw.received_at,
        subject=raw.subject,
        auth_result=raw.auth_results,
    )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_parsers.py -v`
Expected: PASS (22 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/bank_email_parsers.py tests/test_bank_email_parsers.py
git commit -m "feat: parse bank payment notification emails from subject headers"
```

---

## Task 2: Modelos y migración

**Files:**
- Create: `app/models/bank_email.py`
- Create: `alembic/versions/<rev>_add_bank_email_tables.py`
- Modify: `app/models/__init__.py`

**Interfaces:**
- Consumes: nada de Task 1.
- Produces: `BankEmailNotification`, `BankEmailVerification`, `BankEmailVerificationStatus`.

- [ ] **Step 1: Crear los modelos**

Crear `app/models/bank_email.py`:

```python
"""
Confirmación de Zelle contra los correos que mandan los bancos de las cuentas alquiladas.

Dos tablas con papeles distintos:

- `bank_email_notifications`: qué correos llegaron. Las llena el poller; no sabe nada de
  operaciones. `message_id` único hace la ingesta idempotente y `consumed_by_payment_id`
  impide que dos Zelle del mismo monto se confirmen con el mismo correo.
- `bank_email_verifications`: qué pagos estamos esperando confirmar. Una por pago
  entrante reenviado al grupo.
"""

import enum

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, Numeric, String, Text, Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class BankEmailVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    NOT_FOUND = "NOT_FOUND"


class BankEmailNotification(UUIDMixin, Base):
    __tablename__ = "bank_email_notifications"

    id = Column(Integer, primary_key=True, index=True)
    #: Message-ID del correo. Único: es lo que hace la ingesta idempotente.
    message_id = Column(String(255), nullable=False, unique=True, index=True)
    #: Etiqueta legible del buzón ("Jean", "Mariana"): es lo que sale en el aviso.
    mailbox_label = Column(String(60), nullable=False)
    mailbox_email = Column(String(255), nullable=False)
    bank = Column(String(40), nullable=False)
    sender_name = Column(String(200), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, index=True)
    currency = Column(String(10), nullable=False, default="USD")
    received_at = Column(DateTime(timezone=True), nullable=False, index=True)
    subject = Column(Text, nullable=True)
    #: Cabecera Authentication-Results tal cual, para poder auditar después.
    auth_result = Column(Text, nullable=True)
    consumed_by_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BankEmailVerification(UUIDMixin, Base):
    __tablename__ = "bank_email_verifications"

    id = Column(Integer, primary_key=True, index=True)
    incoming_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(
        SAEnum(BankEmailVerificationStatus, name="bank_email_verification_status"),
        nullable=False, default=BankEmailVerificationStatus.PENDING, index=True,
    )
    matched_notification_id = Column(
        Integer, ForeignKey("bank_email_notifications.id", ondelete="SET NULL"), nullable=True,
    )
    requested_at = Column(DateTime(timezone=True), nullable=False)
    #: Índice 0-based dentro de ESCALATION_MINUTES: qué aviso toca mandar.
    escalation_step = Column(Integer, nullable=False, default=0)
    next_notify_at = Column(DateTime(timezone=True), nullable=True, index=True)
    #: Si el buzón no se pudo leer, la escalera se congela hasta acá: el sistema nunca
    #: declara "no confirmado" cuando en realidad no pudo mirar.
    frozen_until = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    notification = relationship("BankEmailNotification", foreign_keys=[matched_notification_id])
```

- [ ] **Step 2: Registrar los modelos**

En `app/models/__init__.py`, agregar el import junto a los otros (antes del `__all__`):

```python
from .bank_email import (
    BankEmailNotification,
    BankEmailVerification,
    BankEmailVerificationStatus,
)
```

Y agregar al final de la lista `__all__`:

```python
           "BankEmailNotification", "BankEmailVerification",
           "BankEmailVerificationStatus"]
```

(o sea: cambiar `"ProfitAllocationDestination"]` por `"ProfitAllocationDestination",` seguido de la línea de arriba.)

- [ ] **Step 3: Averiguar la cabeza actual de Alembic**

Run: `cd backend && alembic heads`
Anotar el id que imprime: es el `down_revision` de la migración nueva. **No inventar el
valor ni copiar uno de otro archivo** — si hay más de una cabeza, parar y avisar.

- [ ] **Step 4: Crear la migración**

Primero elegir un id propio para la revisión nueva y **verificar que no exista ya**:

```bash
cd backend
python -c "import uuid; print(uuid.uuid4().hex[:12])"   # ej. 31ac3d9074b1
grep -rl "<ID_NUEVO>" alembic/versions/                  # tiene que no devolver nada
```

Este repo tiene 59 migraciones con ids "bonitos" (`a4b5c6d7e8f9`, `z3a4b5c6d7e8`…), así
que inventar uno a ojo colisiona fácil — y dos archivos con la misma revisión dejan el
grafo de Alembic ambiguo (`UserWarning: Revision X is present more than once`).

Crear `alembic/versions/<ID_NUEVO>_add_bank_email_tables.py` (reemplazar `<ID_NUEVO>` por
el id verificado y `<HEAD_ANTERIOR>` por el del Step 3):

```python
"""add bank email notifications and verifications

Revision ID: <ID_NUEVO>
Revises: <HEAD_ANTERIOR>
Create Date: 2026-08-04
"""

from alembic import op
import sqlalchemy as sa


revision = "<ID_NUEVO>"
down_revision = "<HEAD_ANTERIOR>"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "bank_email_notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.String(length=255), nullable=False),
        sa.Column("mailbox_label", sa.String(length=60), nullable=False),
        sa.Column("mailbox_email", sa.String(length=255), nullable=False),
        sa.Column("bank", sa.String(length=40), nullable=False),
        sa.Column("sender_name", sa.String(length=200), nullable=True),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False, server_default="USD"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("auth_result", sa.Text(), nullable=True),
        sa.Column("consumed_by_payment_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["consumed_by_payment_id"], ["whatsapp_incoming_payments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bank_email_notifications_uuid", "bank_email_notifications", ["uuid"], unique=True)
    op.create_index("ix_bank_email_notifications_message_id", "bank_email_notifications", ["message_id"], unique=True)
    op.create_index("ix_bank_email_notifications_amount", "bank_email_notifications", ["amount"])
    op.create_index("ix_bank_email_notifications_received_at", "bank_email_notifications", ["received_at"])
    op.create_index(
        "ix_bank_email_notifications_consumed_by_payment_id",
        "bank_email_notifications", ["consumed_by_payment_id"],
    )

    op.create_table(
        "bank_email_verifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("uuid", sa.dialects.postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("incoming_payment_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "CONFIRMED", "NOT_FOUND", name="bank_email_verification_status"),
            nullable=False,
        ),
        sa.Column("matched_notification_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("escalation_step", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_notify_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("frozen_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["incoming_payment_id"], ["whatsapp_incoming_payments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_notification_id"], ["bank_email_notifications.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incoming_payment_id", name="uq_bank_email_verification_payment"),
    )
    op.create_index("ix_bank_email_verifications_uuid", "bank_email_verifications", ["uuid"], unique=True)
    op.create_index("ix_bank_email_verifications_status", "bank_email_verifications", ["status"])
    op.create_index("ix_bank_email_verifications_next_notify_at", "bank_email_verifications", ["next_notify_at"])


def downgrade():
    op.drop_table("bank_email_verifications")
    op.execute("DROP TYPE IF EXISTS bank_email_verification_status")
    op.drop_table("bank_email_notifications")
```

- [ ] **Step 5: Aplicar y verificar la migración**

```bash
cd backend
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```
Expected: las tres corren sin error. El ciclo up→down→up prueba que el `downgrade` sirve.

Verificar las tablas:
```bash
psql postgresql://tasas_user:tasas_password@localhost:5433/tasas_db -c "\d bank_email_notifications"
```
Expected: la tabla existe con el índice único sobre `message_id`.

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/models/bank_email.py app/models/__init__.py alembic/versions/<ID_NUEVO>_add_bank_email_tables.py
git commit -m "feat: add bank email notification and verification tables"
```

> Ejecutado el 2026-08-04: id `31ac3d9074b1`, sobre la cabeza `3e4f5a6b7c8d`.

---

## Task 3: Matching y escalera (lógica pura)

**Files:**
- Create: `app/services/bank_email_matching.py`
- Test: `tests/test_bank_email_matching.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `@dataclass NotificationCandidate(id: int, amount: Decimal, received_at: datetime, mailbox_label: str, sender_name: str, bank: str)`
  - `LOOKBACK_HOURS: int = 12`
  - `ESCALATION_MINUTES: list[int] = [5, 15, 30, 60]`
  - `pick_email_confirmation(candidates, *, amount: Decimal, payment_created_at: datetime, now: datetime) -> tuple[Optional[NotificationCandidate], int]`
  - `schedule_next(step: int, requested_at: datetime) -> Optional[datetime]`
  - `is_final_step(step: int) -> bool`
  - `build_confirmed_message(candidate, *, amount, minutes_elapsed: int, ambiguity_count: int) -> str`
  - `build_escalation_message(step: int, *, amount: Decimal, client_phone: str) -> str`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_matching.py`:

```python
"""
Qué correo confirma qué pago, y cuándo se le insiste al operador
(app/services/bank_email_matching.py).

Puro, sin BD ni red. El reloj entra por parámetro: nada de datetime.now() adentro.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.services.bank_email_matching import (
    ESCALATION_MINUTES,
    NotificationCandidate,
    build_confirmed_message,
    build_escalation_message,
    is_final_step,
    pick_email_confirmation,
    schedule_next,
)

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)
PAID_AT = NOW - timedelta(minutes=20)


def cand(id_, amount, *, minutes_ago=25, label="Jean", name="Carlos R Barrientos"):
    return NotificationCandidate(
        id=id_,
        amount=Decimal(amount),
        received_at=NOW - timedelta(minutes=minutes_ago),
        mailbox_label=label,
        sender_name=name,
        bank="BANK_OF_AMERICA",
    )


def pick(candidates, amount="30.00", paid_at=PAID_AT, now=NOW):
    return pick_email_confirmation(
        candidates, amount=Decimal(amount), payment_created_at=paid_at, now=now
    )


# ---------- pick_email_confirmation ----------

def test_confirma_con_el_correo_del_mismo_monto():
    chosen, count = pick([cand(1, "30.00")])
    assert chosen is not None and chosen.id == 1
    assert count == 1


def test_no_confirma_con_monto_distinto():
    chosen, count = pick([cand(1, "31.00")])
    assert chosen is None and count == 0


def test_no_confirma_con_diferencia_de_un_centavo():
    # Sin tolerancia: los dos lados son dólares con centavos, no hay conversión de por medio.
    chosen, _ = pick([cand(1, "30.01")])
    assert chosen is None


def test_sin_candidatos_no_confirma():
    chosen, count = pick([])
    assert chosen is None and count == 0


def test_ignora_correo_anterior_a_la_ventana():
    # 13 h antes del pago: fuera de las 12 h hacia atrás.
    viejo = cand(1, "30.00", minutes_ago=13 * 60 + 20)
    chosen, count = pick([viejo])
    assert chosen is None and count == 0


def test_acepta_correo_dentro_de_la_ventana_hacia_atras():
    # 11 h antes del pago: adentro.
    chosen, _ = pick([cand(1, "30.00", minutes_ago=11 * 60 + 20)])
    assert chosen is not None


def test_ignora_correo_del_futuro():
    futuro = NotificationCandidate(
        id=1, amount=Decimal("30.00"), received_at=NOW + timedelta(minutes=5),
        mailbox_label="Jean", sender_name="Carlos", bank="BANK_OF_AMERICA",
    )
    chosen, count = pick([futuro])
    assert chosen is None and count == 0


def test_con_dos_candidatos_toma_el_mas_antiguo_y_los_cuenta():
    # El aviso tiene que poder decir "había 2 correos de $30,00 sin asignar".
    chosen, count = pick([cand(2, "30.00", minutes_ago=10), cand(1, "30.00", minutes_ago=25)])
    assert chosen is not None and chosen.id == 1
    assert count == 2


def test_elige_solo_entre_los_del_monto_correcto():
    chosen, count = pick([cand(1, "50.00"), cand(2, "30.00")])
    assert chosen is not None and chosen.id == 2
    assert count == 1


# ---------- escalera ----------

def test_primer_aviso_a_los_cinco_minutos():
    assert schedule_next(0, NOW) == NOW + timedelta(minutes=5)


def test_segundo_aviso_a_los_quince():
    assert schedule_next(1, NOW) == NOW + timedelta(minutes=15)


def test_los_escalones_son_los_del_spec():
    assert ESCALATION_MINUTES == [5, 15, 30, 60]


def test_despues_del_ultimo_escalon_no_hay_mas():
    assert schedule_next(len(ESCALATION_MINUTES), NOW) is None


def test_el_ultimo_escalon_es_final():
    assert is_final_step(len(ESCALATION_MINUTES) - 1) is True


def test_los_escalones_intermedios_no_son_finales():
    assert is_final_step(0) is False
    assert is_final_step(2) is False


# ---------- mensajes ----------

def test_mensaje_de_confirmacion_dice_nombre_cuenta_y_banco():
    text = build_confirmed_message(
        cand(1, "30.00"), amount=Decimal("30.00"), minutes_elapsed=22, ambiguity_count=1
    )
    assert "Carlos R Barrientos" in text
    assert "Jean" in text
    assert "30,00" in text
    assert "22 min" in text
    assert "⚠️" not in text


def test_mensaje_de_confirmacion_avisa_si_habia_varios_candidatos():
    text = build_confirmed_message(
        cand(1, "30.00"), amount=Decimal("30.00"), minutes_elapsed=1, ambiguity_count=2
    )
    assert "2 correos" in text


def test_mensaje_de_escalon_incluye_los_minutos():
    text = build_escalation_message(0, amount=Decimal("30.00"), client_phone="584121234567")
    assert "5 min" in text
    assert "30,00" in text


def test_ultimo_escalon_avisa_que_cierra():
    text = build_escalation_message(3, amount=Decimal("30.00"), client_phone="584121234567")
    assert "1 h" in text
    assert "cierro" in text.lower()
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_matching.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bank_email_matching'`

- [ ] **Step 3: Implementar**

Crear `app/services/bank_email_matching.py`:

```python
"""
Reglas de la confirmación por correo: qué notificación confirma qué pago, cuándo se le
insiste al operador y qué dice cada aviso.

Puro y con el reloj inyectado: es la parte que puede equivocarse, así que tiene que
poder probarse sin BD, sin red y sin esperar una hora.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

#: Cuánto hacia atrás se aceptan correos respecto del comprobante. Cubre que el reenvío
#: al grupo ocurra mucho después de que el cliente pagó.
LOOKBACK_HOURS = 12

#: Minutos desde el reenvío en los que se le insiste al operador si no aparece el correo.
#: La búsqueda corre cada minuto igual; esto es solo cuándo se avisa.
ESCALATION_MINUTES = [5, 15, 30, 60]


@dataclass
class NotificationCandidate:
    id: int
    amount: Decimal
    received_at: datetime
    mailbox_label: str
    sender_name: str
    bank: str


def _fmt(amount: Decimal) -> str:
    """30.00 → '30,00' (formato venezolano, como el resto de los avisos del bot)."""
    return f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def pick_email_confirmation(
    candidates: list[NotificationCandidate],
    *,
    amount: Decimal,
    payment_created_at: datetime,
    now: datetime,
) -> tuple[Optional[NotificationCandidate], int]:
    """
    Elige el correo que confirma un pago. Devuelve (elegido, cuántos había en ventana).

    `candidates` ya viene filtrado por SQL a notificaciones SIN consumir. Acá se aplican
    monto exacto y ventana; con varios se toma el más antiguo y el contador deja que el
    llamador avise de la ambigüedad — una confirmación equivocada haría entregar Bs de más.
    """
    floor = payment_created_at - timedelta(hours=LOOKBACK_HOURS)
    in_window = [
        c for c in candidates
        if c.amount == amount and floor <= c.received_at <= now
    ]
    if not in_window:
        return None, 0
    in_window.sort(key=lambda c: c.received_at)
    return in_window[0], len(in_window)


def schedule_next(step: int, requested_at: datetime) -> Optional[datetime]:
    """Cuándo toca el aviso del escalón `step` (0-based). None si ya no hay más."""
    if step < 0 or step >= len(ESCALATION_MINUTES):
        return None
    return requested_at + timedelta(minutes=ESCALATION_MINUTES[step])


def is_final_step(step: int) -> bool:
    return step >= len(ESCALATION_MINUTES) - 1


def build_confirmed_message(
    candidate: NotificationCandidate,
    *,
    amount: Decimal,
    minutes_elapsed: int,
    ambiguity_count: int,
) -> str:
    bank = "Bank of America" if candidate.bank == "BANK_OF_AMERICA" else candidate.bank
    when = candidate.received_at.strftime("%d/%m %H:%M")
    lines = [
        f"✅ *Pago confirmado por correo* — ${_fmt(amount)}",
        f"👤 A nombre de *{candidate.sender_name}*",
        f"🏦 En la cuenta de *{candidate.mailbox_label}* ({bank}, {when})",
    ]
    if minutes_elapsed > 0:
        lines.append(f"⏱️ Tardó {minutes_elapsed} min en aparecer")
    if ambiguity_count > 1:
        lines.append(
            f"⚠️ Había {ambiguity_count} correos de ${_fmt(amount)} sin asignar; "
            f"tomé el de las {candidate.received_at.strftime('%H:%M')}"
        )
    return "\n".join(lines)


def build_escalation_message(step: int, *, amount: Decimal, client_phone: str) -> str:
    minutes = ESCALATION_MINUTES[step]
    label = "1 h" if minutes >= 60 else f"{minutes} min"
    if is_final_step(step):
        return (
            f"🚨 *${_fmt(amount)} SIN CONFIRMAR* ({label}) — el comprobante de "
            f"{client_phone} no apareció en ningún correo. Cierro la verificación."
        )
    icon = "⏳" if step == 0 else "⚠️"
    return (
        f"{icon} ${_fmt(amount)} sigue sin aparecer en los correos ({label}) — "
        f"comprobante de {client_phone}"
    )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_matching.py -v`
Expected: PASS (20 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/bank_email_matching.py tests/test_bank_email_matching.py
git commit -m "feat: add matching rules and escalation ladder for email confirmation"
```

---

## Task 4: Configuración de buzones

**Files:**
- Modify: `app/core/config.py`
- Test: `tests/test_bank_email_config.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `settings.ZELLE_MAILBOXES: Optional[str]` (JSON crudo)
  - `@dataclass MailboxConfig(label: str, email: str, password: str)` en `app/core/config.py`
  - `parse_mailboxes(raw: Optional[str]) -> list[MailboxConfig]`
  - `settings.mailboxes_computed -> list[MailboxConfig]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_config.py`:

```python
"""Parseo de ZELLE_MAILBOXES (app/core/config.py)."""

import pytest

from app.core.config import MailboxConfig, parse_mailboxes


def test_parsea_dos_buzones():
    raw = (
        '[{"label":"Jean","email":"azocarjean98@gmail.com","password":"aaaa bbbb cccc dddd"},'
        ' {"label":"Mariana","email":"mmendozaperez53@gmail.com","password":"eeee ffff"}]'
    )
    boxes = parse_mailboxes(raw)
    assert boxes == [
        MailboxConfig(label="Jean", email="azocarjean98@gmail.com", password="aaaa bbbb cccc dddd"),
        MailboxConfig(label="Mariana", email="mmendozaperez53@gmail.com", password="eeee ffff"),
    ]


def test_sin_configuracion_devuelve_lista_vacia():
    # La feature apagada no debe romper el arranque del backend.
    assert parse_mailboxes(None) == []
    assert parse_mailboxes("") == []
    assert parse_mailboxes("   ") == []


def test_json_invalido_explota_con_mensaje_claro():
    with pytest.raises(ValueError, match="ZELLE_MAILBOXES"):
        parse_mailboxes("{no es json}")


def test_falta_un_campo_explota():
    with pytest.raises(ValueError, match="password"):
        parse_mailboxes('[{"label":"Jean","email":"a@b.com"}]')


def test_no_es_una_lista_explota():
    with pytest.raises(ValueError, match="lista"):
        parse_mailboxes('{"label":"Jean","email":"a@b.com","password":"x"}')
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'MailboxConfig'`

- [ ] **Step 3: Implementar**

En `app/core/config.py`, agregar arriba (junto a los otros imports):

```python
import json
from dataclasses import dataclass
```

Agregar antes de la clase `Settings`:

```python
@dataclass(frozen=True)
class MailboxConfig:
    """Un buzón de una cuenta alquilada. `label` es lo que ve el operador en el aviso."""

    label: str
    email: str
    password: str


def parse_mailboxes(raw: Optional[str]) -> list["MailboxConfig"]:
    """
    Parsea ZELLE_MAILBOXES. Vacío = feature apagada (no rompe el arranque).

    Se valida acá y no al usarlo: un typo en el .env tiene que fallar fuerte y claro,
    no convertirse en confirmaciones que nunca llegan.
    """
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"ZELLE_MAILBOXES no es JSON válido: {e}") from e

    if not isinstance(data, list):
        raise ValueError("ZELLE_MAILBOXES debe ser una lista de objetos")

    boxes: list[MailboxConfig] = []
    for i, item in enumerate(data):
        for field in ("label", "email", "password"):
            if not isinstance(item, dict) or not item.get(field):
                raise ValueError(f"ZELLE_MAILBOXES[{i}]: falta el campo '{field}'")
        boxes.append(
            MailboxConfig(label=item["label"], email=item["email"], password=item["password"])
        )
    return boxes
```

Dentro de la clase `Settings`, junto al bloque del bot de WhatsApp:

```python
    # =================
    # BUZONES DE LAS CUENTAS ALQUILADAS (confirmación de Zelle por correo)
    # =================
    # JSON: [{"label":"Jean","email":"...@gmail.com","password":"<app password de Gmail>"}]
    # Vacío = confirmación por correo apagada.
    ZELLE_MAILBOXES: Optional[str] = None
```

Y como propiedad de `Settings` (junto a `celery_broker_url_computed`):

```python
    @property
    def mailboxes_computed(self) -> list[MailboxConfig]:
        return parse_mailboxes(self.ZELLE_MAILBOXES)
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_config.py -v`
Expected: PASS (5 tests)

Verificar que el backend sigue arrancando sin la variable:
Run: `cd backend && python -c "from app.core.config import settings; print(settings.mailboxes_computed)"`
Expected: `[]`

- [ ] **Step 5: Documentar la variable**

Agregar a `.env.example` (y a `.env` local con los valores reales, que NO se commitea):

```
# Confirmación de Zelle por correo. JSON con un objeto por cuenta alquilada.
# La contraseña es una "contraseña de aplicación" de Gmail (requiere 2FA activo).
ZELLE_MAILBOXES=[{"label":"Jean","email":"azocarjean98@gmail.com","password":"xxxx xxxx xxxx xxxx"}]
```

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/core/config.py tests/test_bank_email_config.py .env.example
git commit -m "feat: add ZELLE_MAILBOXES configuration for bank email confirmation"
```

---

## Task 5: Capa IMAP

**Files:**
- Create: `app/services/bank_email_imap.py`
- Test: `tests/test_bank_email_imap.py`

**Interfaces:**
- Consumes: `RawEmailHeaders` (Task 1), `MailboxConfig` (Task 4).
- Produces:
  - `class MailboxUnavailable(Exception)`
  - `headers_from_message(msg: email.message.Message, fallback_to: str) -> Optional[RawEmailHeaders]`
  - `fetch_recent_headers(box: MailboxConfig, *, since_days: int = 1) -> list[RawEmailHeaders]`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_imap.py`:

```python
"""
Conversión de un mensaje de correo crudo a RawEmailHeaders
(app/services/bank_email_imap.py).

La conexión IMAP en sí no se testea: se prueba a mano con `python -m app.cli.check_mailboxes`.
Lo que sí se prueba es el parseo de cabeceras, que es donde están los detalles feos
(fechas con zona, asuntos codificados en MIME, Message-ID ausente).
"""

from datetime import timezone
from email.message import EmailMessage

from app.services.bank_email_imap import headers_from_message


def make(**kw) -> EmailMessage:
    msg = EmailMessage()
    msg["Message-ID"] = kw.get("message_id", "<abc123@bankofamerica.com>")
    msg["Subject"] = kw.get("subject", "Carlos R Barrientos le envió $30.00")
    msg["From"] = kw.get("from_", "Bank of America <customerservice@ealerts.bankofamerica.com>")
    msg["To"] = kw.get("to", "azocarjean98@gmail.com")
    msg["Date"] = kw.get("date", "Tue, 4 Aug 2026 14:01:03 -0400")
    if kw.get("auth") is not False:
        msg["Authentication-Results"] = kw.get("auth", "mx.google.com; dkim=pass header.i=@bankofamerica.com")
    return msg


def test_extrae_las_cabeceras_basicas():
    raw = headers_from_message(make(), fallback_to="azocarjean98@gmail.com")
    assert raw is not None
    assert raw.message_id == "<abc123@bankofamerica.com>"
    assert raw.subject == "Carlos R Barrientos le envió $30.00"
    assert raw.to_addr == "azocarjean98@gmail.com"


def test_extrae_solo_la_direccion_del_from():
    # El From viene como 'Nombre <dir@dominio>'; la plantilla compara contra la dirección sola.
    raw = headers_from_message(make(), fallback_to="azocarjean98@gmail.com")
    assert raw.from_addr == "customerservice@ealerts.bankofamerica.com"


def test_normaliza_la_fecha_a_utc():
    raw = headers_from_message(make(), fallback_to="x@y.com")
    assert raw.received_at.tzinfo is not None
    assert raw.received_at.utcoffset().total_seconds() == 0
    # 14:01 -0400 == 18:01 UTC
    assert raw.received_at.astimezone(timezone.utc).hour == 18


def test_decodifica_asunto_codificado_en_mime():
    encoded = "=?UTF-8?Q?Carlos_R_Barrientos_le_envi=C3=B3_=2430=2E00?="
    raw = headers_from_message(make(subject=encoded), fallback_to="x@y.com")
    assert raw.subject == "Carlos R Barrientos le envió $30.00"


def test_usa_el_buzon_como_to_si_falta_la_cabecera():
    # Gmail a veces entrega el correo sin To visible (BCC, alias).
    msg = make()
    del msg["To"]
    raw = headers_from_message(msg, fallback_to="azocarjean98@gmail.com")
    assert raw.to_addr == "azocarjean98@gmail.com"


def test_sin_authentication_results_devuelve_cadena_vacia():
    raw = headers_from_message(make(auth=False), fallback_to="x@y.com")
    assert raw.auth_results == ""


def test_sin_message_id_se_descarta():
    # Sin Message-ID no hay idempotencia posible: se prefiere perder el correo a duplicarlo.
    msg = make()
    del msg["Message-ID"]
    assert headers_from_message(msg, fallback_to="x@y.com") is None


def test_sin_fecha_se_descarta():
    msg = make()
    del msg["Date"]
    assert headers_from_message(msg, fallback_to="x@y.com") is None
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_imap.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bank_email_imap'`

- [ ] **Step 3: Implementar**

Crear `app/services/bank_email_imap.py`:

```python
"""
Único lugar del backend que habla IMAP.

Se trae SOLO cabeceras y con BODY.PEEK: `PEEK` es obligatorio porque no marca los correos
como leídos — el operador sigue viendo su bandeja igual que siempre.

Todo lo demás (parseo del asunto, autenticidad, matching) vive en módulos puros que se
prueban sin red. Acá solo hay I/O y conversión de cabeceras.
"""

import email
import imaplib
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr
from typing import Optional

from app.core.config import MailboxConfig
from app.services.bank_email_parsers import RawEmailHeaders

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_TIMEOUT_SECONDS = 20


class MailboxUnavailable(Exception):
    """No se pudo leer el buzón (credenciales, red, Gmail caído)."""


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def headers_from_message(msg, fallback_to: str) -> Optional[RawEmailHeaders]:
    """
    Convierte un mensaje ya parseado en RawEmailHeaders, o None si le falta lo mínimo.

    Sin Message-ID no hay idempotencia posible, y sin Date no hay ventana: en ambos casos
    se prefiere descartar el correo a ingerir basura.
    """
    message_id = (msg.get("Message-ID") or "").strip()
    if not message_id:
        return None

    raw_date = msg.get("Date")
    if not raw_date:
        return None
    try:
        received_at = parsedate_to_datetime(raw_date)
    except (TypeError, ValueError):
        return None
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=timezone.utc)
    received_at = received_at.astimezone(timezone.utc)

    _, from_addr = parseaddr(msg.get("From") or "")
    _, to_addr = parseaddr(msg.get("To") or "")

    return RawEmailHeaders(
        message_id=message_id,
        subject=_decode(msg.get("Subject")),
        from_addr=from_addr.lower(),
        to_addr=(to_addr or fallback_to).lower(),
        received_at=received_at,
        auth_results=_decode(msg.get("Authentication-Results")),
    )


def fetch_recent_headers(box: MailboxConfig, *, since_days: int = 1) -> list[RawEmailHeaders]:
    """
    Cabeceras de los correos recientes de INBOX. Nunca toca Spam ni marca como leído.

    Lanza MailboxUnavailable si el buzón no se pudo leer: el llamador tiene que poder
    distinguir "no llegó el correo" de "no pude mirar".
    """
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%d-%b-%Y")
    conn = None
    try:
        conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=IMAP_TIMEOUT_SECONDS)
        conn.login(box.email, box.password)
        conn.select("INBOX", readonly=True)

        status, data = conn.search(None, "SINCE", since)
        if status != "OK":
            raise MailboxUnavailable(f"SEARCH devolvió {status} en {box.label}")

        results: list[RawEmailHeaders] = []
        for num in (data[0] or b"").split():
            status, payload = conn.fetch(num, "(BODY.PEEK[HEADER])")
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            msg = email.message_from_bytes(payload[0][1])
            headers = headers_from_message(msg, fallback_to=box.email)
            if headers is not None:
                results.append(headers)
        return results
    except MailboxUnavailable:
        raise
    except Exception as e:
        raise MailboxUnavailable(f"No se pudo leer el buzón de {box.label}: {e}") from e
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            try:
                conn.logout()
            except Exception:
                pass
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_imap.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/bank_email_imap.py tests/test_bank_email_imap.py
git commit -m "feat: add IMAP header reader for rented-account mailboxes"
```

---

## Task 6: Notificador al operador

**Files:**
- Create: `app/services/bot_notifier.py`
- Modify: `app/services/alert_service.py:84-96`
- Test: `tests/test_bot_notifier.py`

**Interfaces:**
- Consumes: `settings.BOT_NOTIFY_URL`, `settings.BOT_API_KEY`.
- Produces: `async def notify_operator(text: str) -> bool`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bot_notifier.py`:

```python
"""Aviso al operador vía el bot (app/services/bot_notifier.py)."""

import pytest

from app.services import bot_notifier


@pytest.mark.asyncio
async def test_sin_configuracion_no_intenta_y_devuelve_false(monkeypatch):
    # Sin BOT_NOTIFY_URL la feature está apagada; no debe explotar ni intentar red.
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", None)
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", "token")
    assert await bot_notifier.notify_operator("hola") is False


@pytest.mark.asyncio
async def test_sin_token_no_intenta(monkeypatch):
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", "http://localhost:3457")
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", None)
    assert await bot_notifier.notify_operator("hola") is False


@pytest.mark.asyncio
async def test_error_de_red_devuelve_false_sin_propagar(monkeypatch):
    # Un aviso que no sale nunca debe tumbar el poller: se reintenta en la vuelta siguiente.
    monkeypatch.setattr(bot_notifier.settings, "BOT_NOTIFY_URL", "http://127.0.0.1:9")
    monkeypatch.setattr(bot_notifier.settings, "BOT_API_KEY", "token")
    assert await bot_notifier.notify_operator("hola") is False
```

Nota: `pytest.ini` tiene `asyncio_mode = strict`, así que el decorador `@pytest.mark.asyncio` es obligatorio.

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bot_notifier.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bot_notifier'`

- [ ] **Step 3: Implementar**

Crear `app/services/bot_notifier.py`:

```python
"""
Mandarle un mensaje de WhatsApp al operador a través del bot.

El bot expone /api/notify en su dashboard (BOT_NOTIFY_URL, típicamente :3457) protegido
con X-Bot-Token. Nunca propaga excepciones: un aviso que no sale no puede tumbar al que
lo estaba mandando.
"""

import aiohttp

from app.core.config import settings

TIMEOUT_SECONDS = 5


async def notify_operator(text: str) -> bool:
    """Devuelve True si el bot aceptó el mensaje."""
    if not settings.BOT_NOTIFY_URL or not settings.BOT_API_KEY:
        return False

    url = f"{settings.BOT_NOTIFY_URL.rstrip('/')}/api/notify"
    try:
        timeout = aiohttp.ClientTimeout(total=TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json={"text": text},
                headers={"X-Bot-Token": settings.BOT_API_KEY},
            ) as resp:
                if resp.status != 200:
                    print(f"⚠️ Notificación al bot falló: HTTP {resp.status}")
                    return False
                return True
    except Exception as e:
        print(f"⚠️ Notificación al bot falló: {e}")
        return False
```

- [ ] **Step 4: Borrar la duplicación en alert_service**

En `app/services/alert_service.py`, reemplazar el método `_post_to_bot` completo (líneas ~84-96) por:

```python
    async def _post_to_bot(self, url: str, text: str) -> None:
        # `url` se ignora: bot_notifier arma el endpoint desde BOT_NOTIFY_URL. Se conserva
        # el parámetro para no tocar los llamadores.
        await notify_operator(text)
```

Y agregar el import junto a los otros:

```python
from app.services.bot_notifier import notify_operator
```

Si `aiohttp` queda sin usarse en `alert_service.py`, borrar su import.

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bot_notifier.py -v`
Expected: PASS (3 tests)

Run: `cd backend && python -m pytest tests/ -q`
Expected: la suite entera sigue en verde (el cambio en `alert_service` no rompió nada).

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/services/bot_notifier.py app/services/alert_service.py tests/test_bot_notifier.py
git commit -m "feat: extract operator notification into a reusable service"
```

---

## Task 7: Servicio de ingesta

**Files:**
- Create: `app/services/bank_email_service.py`
- Test: `tests/test_bank_email_ingest.py`

**Interfaces:**
- Consumes: `parse_bank_email`, `authentication_ok`, `find_template` (Task 1); modelos (Task 2); `MailboxConfig` (Task 4); `fetch_recent_headers`, `MailboxUnavailable` (Task 5).
- Produces:
  - `@dataclass RejectedEmail(message_id: str, text: str)`
  - `class BankEmailService(db: Session)`
  - `.ingest_headers(headers: list[RawEmailHeaders], mailbox_label: str) -> tuple[int, list[RejectedEmail]]` → (insertados, rechazos a avisar)
  - `.ingest_mailbox(box: MailboxConfig) -> tuple[int, list[RejectedEmail]]` (puede lanzar `MailboxUnavailable`)

**Por qué los rechazos llevan `message_id`:** un correo falsificado que se quede en la
bandeja se vuelve a leer en cada vuelta del poller. Sin una forma de identificarlo, avisaría
cada 60 segundos durante 24 horas. El `message_id` deja que el llamador avise una sola vez.

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_ingest.py`:

```python
"""
Ingesta de correos a bank_email_notifications (app/services/bank_email_service.py).

Integración contra Postgres: usa las fixtures de conftest.py y se salta solo si no hay
Postgres local en :5433.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.models.bank_email import BankEmailNotification
from app.services.bank_email_parsers import RawEmailHeaders
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 1, tzinfo=timezone.utc)
BOA_AUTH = "mx.google.com; dkim=pass header.i=@bankofamerica.com; spf=pass"


def raw(**kw) -> RawEmailHeaders:
    base = dict(
        message_id="<abc@bankofamerica.com>",
        subject="Carlos R Barrientos le envió $30.00",
        from_addr="customerservice@ealerts.bankofamerica.com",
        to_addr="azocarjean98@gmail.com",
        received_at=NOW,
        auth_results=BOA_AUTH,
    )
    base.update(kw)
    return RawEmailHeaders(**base)


def test_inserta_una_notificacion(db):
    inserted, warnings = BankEmailService(db).ingest_headers([raw()], mailbox_label="Jean")

    assert inserted == 1
    assert warnings == []
    row = db.query(BankEmailNotification).one()
    assert row.amount == Decimal("30.00")
    assert row.sender_name == "Carlos R Barrientos"
    assert row.mailbox_label == "Jean"
    assert row.consumed_by_payment_id is None


def test_no_duplica_el_mismo_message_id(db):
    service = BankEmailService(db)
    service.ingest_headers([raw()], mailbox_label="Jean")
    inserted, _ = service.ingest_headers([raw()], mailbox_label="Jean")

    assert inserted == 0
    assert db.query(BankEmailNotification).count() == 1


def test_ignora_correos_que_no_son_notificaciones(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(subject="Your monthly statement is ready")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert warnings == []  # no es un pago: ni se avisa
    assert db.query(BankEmailNotification).count() == 0


def test_descarta_y_avisa_cuando_falla_la_autenticacion(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(auth_results="dkim=fail; spf=softfail")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert db.query(BankEmailNotification).count() == 0
    assert len(warnings) == 1
    assert "autenticación" in warnings[0].text
    # El message_id viaja con el aviso para poder no repetirlo cada minuto.
    assert warnings[0].message_id == "<abc@bankofamerica.com>"


def test_descarta_reenviados_sin_avisar(db):
    inserted, warnings = BankEmailService(db).ingest_headers(
        [raw(subject="Fwd: Carlos R Barrientos le envió $30.00")], mailbox_label="Jean"
    )
    assert inserted == 0
    assert warnings == []


def test_ingiere_varios_de_una(db):
    inserted, _ = BankEmailService(db).ingest_headers(
        [
            raw(),
            raw(message_id="<def@1stnb.com>", subject="Notification - Aristides Bravo sent you $107.00.",
                from_addr="custserv@1stnb.com",
                auth_results="mx.google.com; spf=pass smtp.mailfrom=1stnb.com"),
        ],
        mailbox_label="Mariana",
    )
    assert inserted == 2
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_ingest.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.bank_email_service'`

(Si imprime "Postgres local (:5433) no disponible", levantarlo antes: `docker-compose up -d db`.)

- [ ] **Step 3: Implementar**

Crear `app/services/bank_email_service.py`:

```python
"""
Confirmación de Zelle contra los correos de los bancos: ingesta.

La ingesta no sabe nada de operaciones ni de comprobantes — solo convierte correos en
filas. La resolución (qué correo confirma qué pago) va aparte, en este mismo módulo pero
en métodos distintos, y usa las reglas puras de bank_email_matching.
"""

from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import MailboxConfig
from app.models.bank_email import BankEmailNotification
from app.services.bank_email_imap import fetch_recent_headers
from app.services.bank_email_parsers import (
    RawEmailHeaders,
    authentication_ok,
    find_template,
    parse_bank_email,
)


@dataclass
class RejectedEmail:
    """Un correo que parecía un pago pero no pasó autenticación."""

    message_id: str
    text: str


class BankEmailService:
    def __init__(self, db: Session):
        self.db = db

    def ingest_headers(
        self, headers: list[RawEmailHeaders], mailbox_label: str
    ) -> tuple[int, list[RejectedEmail]]:
        """
        Guarda las notificaciones de pago que vengan en `headers`.

        Devuelve (cuántas se insertaron, rechazos para avisar). Los rechazos son los
        correos que PARECÍAN pagos pero no pasaron autenticación: callarlos dejaría al
        operador esperando confirmaciones que nunca van a llegar.
        """
        inserted = 0
        warnings: list[RejectedEmail] = []

        for raw in headers:
            parsed = parse_bank_email(raw, mailbox_label)
            if parsed is None:
                continue

            template = find_template(raw.from_addr)
            if template is None or not authentication_ok(raw.auth_results, template.auth_domain):
                warnings.append(
                    RejectedEmail(
                        message_id=parsed.message_id,
                        text=(
                            f"⚠️ Correo de pago descartado por autenticación en la cuenta de "
                            f"{mailbox_label} (de {raw.from_addr}): {raw.subject}"
                        ),
                    )
                )
                continue

            exists = (
                self.db.query(BankEmailNotification.id)
                .filter(BankEmailNotification.message_id == parsed.message_id)
                .first()
            )
            if exists:
                continue

            self.db.add(
                BankEmailNotification(
                    message_id=parsed.message_id,
                    mailbox_label=parsed.mailbox_label,
                    mailbox_email=parsed.mailbox_email,
                    bank=parsed.bank,
                    sender_name=parsed.sender_name,
                    amount=parsed.amount,
                    currency=parsed.currency,
                    received_at=parsed.received_at,
                    subject=parsed.subject,
                    auth_result=parsed.auth_result,
                )
            )
            inserted += 1

        if inserted or warnings:
            self.db.commit()
        return inserted, warnings

    def ingest_mailbox(self, box: MailboxConfig) -> tuple[int, list[str]]:
        """Lee un buzón y lo ingiere. Propaga MailboxUnavailable a propósito."""
        headers = fetch_recent_headers(box)
        return self.ingest_headers(headers, mailbox_label=box.label)
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_ingest.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/bank_email_service.py tests/test_bank_email_ingest.py
git commit -m "feat: ingest bank notification emails into the database"
```

---

## Task 8: Verificación, resolución y escalera

**Files:**
- Modify: `app/services/bank_email_service.py`
- Test: `tests/test_bank_email_verification.py`

**Interfaces:**
- Consumes: todo lo de Tasks 1-7.
- Produces, en `BankEmailService`:
  - `.request_verification(payment_id: int, *, now: datetime) -> dict` → `{"status": "confirmed"|"pending"|"skipped"|"not_found", "message": Optional[str]}`
  - `.resolve_pending(*, now: datetime, notify: Callable[[str], bool]) -> list[str]` → avisos efectivamente entregados
  - `.escalate_pending(*, now: datetime, notify: Callable[[str], bool]) -> list[str]` → avisos efectivamente entregados
  - `.freeze_pending(*, now: datetime, minutes: int) -> int`

**Por qué `notify` entra como parámetro en vez de que el llamador mande los mensajes
después:** el spec exige que un aviso que no sale se reintente en la vuelta siguiente. Si
el servicio commitea el cambio de estado y el POST al bot falla después, ese aviso se
pierde para siempre — la verificación ya quedó confirmada o escalada. Con el notificador
adentro, el cambio de estado solo se commitea si el aviso llegó; si no, se revierte y la
vuelta siguiente lo reintenta idéntico (buscar el mismo correo y confirmarlo de nuevo es
idempotente).

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_verification.py`:

```python
"""
Verificación de un pago contra los correos: confirmación inmediata, reintentos y escalera
(app/services/bank_email_service.py).

Integración contra Postgres. El reloj entra siempre por parámetro `now`, así que la
escalera de una hora se prueba en milisegundos.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.models.bank_email import (
    BankEmailNotification,
    BankEmailVerification,
    BankEmailVerificationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)


class Notifier:
    """Notificador de mentira. `ok=False` simula el bot caído."""

    def __init__(self, ok: bool = True):
        self.sent: list[str] = []
        self.ok = ok

    def __call__(self, text: str) -> bool:
        if self.ok:
            self.sent.append(text)
        return self.ok


@pytest.fixture
def payment(db):
    p = WhatsAppIncomingPayment(
        client_phone="584121234567",
        provider="zelle",
        amount=30.0,
        currency="ZELLE",
        raw_text="captura",
        created_at=NOW - timedelta(minutes=10),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


def add_notification(db, *, amount="30.00", minutes_ago=5, label="Jean", message_id="<m1@boa>"):
    n = BankEmailNotification(
        message_id=message_id,
        mailbox_label=label,
        mailbox_email="azocarjean98@gmail.com",
        bank="BANK_OF_AMERICA",
        sender_name="Carlos R Barrientos",
        amount=Decimal(amount),
        currency="USD",
        received_at=NOW - timedelta(minutes=minutes_ago),
        subject="Carlos R Barrientos le envió $30.00",
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return n


# ---------- request_verification ----------

def test_confirma_al_instante_si_el_correo_ya_esta(db, payment):
    notification = add_notification(db)

    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "confirmed"
    assert "Carlos R Barrientos" in result["message"]
    assert "Jean" in result["message"]

    db.refresh(notification)
    assert notification.consumed_by_payment_id == payment.id

    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.CONFIRMED
    assert v.matched_notification_id == notification.id


def test_queda_pendiente_si_no_hay_correo(db, payment):
    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "pending"
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.PENDING
    assert v.escalation_step == 0
    assert v.next_notify_at == NOW + timedelta(minutes=5)


def test_no_verifica_un_pago_sin_monto(db):
    p = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=None,
        currency="ZELLE", raw_text="captura",
    )
    db.add(p)
    db.commit()

    result = BankEmailService(db).request_verification(p.id, now=NOW)

    assert result["status"] == "skipped"
    assert db.query(BankEmailVerification).count() == 0


def test_reenviar_dos_veces_no_consume_otro_correo(db, payment):
    add_notification(db)
    add_notification(db, message_id="<m2@boa>", minutes_ago=3)
    service = BankEmailService(db)

    first = service.request_verification(payment.id, now=NOW)
    second = service.request_verification(payment.id, now=NOW + timedelta(minutes=1))

    assert first["status"] == "confirmed"
    assert second["status"] == "confirmed"
    consumed = db.query(BankEmailNotification).filter(
        BankEmailNotification.consumed_by_payment_id.isnot(None)
    ).count()
    assert consumed == 1


def test_dos_pagos_del_mismo_monto_no_comparten_correo(db, payment):
    add_notification(db)
    otro = WhatsAppIncomingPayment(
        client_phone="584129999999", provider="zelle", amount=30.0,
        currency="ZELLE", raw_text="captura2", created_at=NOW - timedelta(minutes=5),
    )
    db.add(otro)
    db.commit()
    db.refresh(otro)
    service = BankEmailService(db)

    assert service.request_verification(payment.id, now=NOW)["status"] == "confirmed"
    assert service.request_verification(otro.id, now=NOW)["status"] == "pending"


def test_avisa_cuando_habia_dos_correos_candidatos(db, payment):
    add_notification(db, minutes_ago=8, message_id="<m1@boa>")
    add_notification(db, minutes_ago=4, message_id="<m2@boa>")

    result = BankEmailService(db).request_verification(payment.id, now=NOW)

    assert result["status"] == "confirmed"
    assert "2 correos" in result["message"]


# ---------- resolve_pending ----------

def test_resolve_confirma_cuando_llega_el_correo(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    add_notification(db, minutes_ago=0)
    notifier = Notifier()

    messages = service.resolve_pending(now=NOW + timedelta(minutes=3), notify=notifier)

    assert len(messages) == 1
    assert "confirmado" in messages[0].lower()
    assert notifier.sent == messages
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.CONFIRMED


def test_resolve_no_dice_nada_si_sigue_sin_correo(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.resolve_pending(now=NOW + timedelta(minutes=3), notify=Notifier()) == []


def test_si_el_aviso_no_sale_la_confirmacion_se_reintenta(db, payment):
    # Bot caído: no se puede dar por confirmada una verificación cuyo aviso nunca llegó,
    # porque el aviso es el producto entero de esta feature.
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    add_notification(db, minutes_ago=0)

    caido = Notifier(ok=False)
    assert service.resolve_pending(now=NOW + timedelta(minutes=3), notify=caido) == []

    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.PENDING
    assert db.query(BankEmailNotification).one().consumed_by_payment_id is None

    # La vuelta siguiente, con el bot de vuelta, confirma igual.
    bueno = Notifier()
    messages = service.resolve_pending(now=NOW + timedelta(minutes=4), notify=bueno)
    assert len(messages) == 1
    assert db.query(BankEmailVerification).one().status == BankEmailVerificationStatus.CONFIRMED


# ---------- escalate_pending ----------

def test_no_avisa_antes_del_primer_escalon(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.escalate_pending(now=NOW + timedelta(minutes=4), notify=Notifier()) == []


def test_avisa_a_los_cinco_minutos_y_avanza(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    messages = service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier())

    assert len(messages) == 1 and "5 min" in messages[0]
    v = db.query(BankEmailVerification).one()
    assert v.escalation_step == 1
    assert v.next_notify_at == NOW + timedelta(minutes=15)
    assert v.status == BankEmailVerificationStatus.PENDING


def test_si_el_aviso_no_sale_el_escalon_no_avanza(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    assert service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier(ok=False)) == []

    v = db.query(BankEmailVerification).one()
    assert v.escalation_step == 0
    assert v.next_notify_at == NOW + timedelta(minutes=5)


def test_a_la_hora_cierra_la_verificacion(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    for minutes in (5, 15, 30):
        service.escalate_pending(now=NOW + timedelta(minutes=minutes), notify=Notifier())

    messages = service.escalate_pending(now=NOW + timedelta(minutes=60), notify=Notifier())

    assert len(messages) == 1 and "SIN CONFIRMAR" in messages[0]
    v = db.query(BankEmailVerification).one()
    assert v.status == BankEmailVerificationStatus.NOT_FOUND
    assert v.resolved_at is not None


def test_no_sigue_avisando_despues_de_cerrar(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    for minutes in (5, 15, 30, 60):
        service.escalate_pending(now=NOW + timedelta(minutes=minutes), notify=Notifier())

    assert service.escalate_pending(now=NOW + timedelta(minutes=120), notify=Notifier()) == []


# ---------- congelado ----------

def test_buzon_caido_congela_la_escalera(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)

    frozen = service.freeze_pending(now=NOW, minutes=10)

    assert frozen == 1
    # Con el buzón caído, a los 5 min NO se acusa al pago de no existir.
    assert service.escalate_pending(now=NOW + timedelta(minutes=5), notify=Notifier()) == []


def test_al_descongelarse_vuelve_a_avisar(db, payment):
    service = BankEmailService(db)
    service.request_verification(payment.id, now=NOW)
    service.freeze_pending(now=NOW, minutes=10)

    messages = service.escalate_pending(now=NOW + timedelta(minutes=11), notify=Notifier())

    assert len(messages) == 1
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_verification.py -v`
Expected: FAIL — `AttributeError: 'BankEmailService' object has no attribute 'request_verification'`

- [ ] **Step 3: Implementar**

Agregar a `app/services/bank_email_service.py` los imports que faltan:

```python
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Callable

from app.models.bank_email import (
    BankEmailNotification,
    BankEmailVerification,
    BankEmailVerificationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.bank_email_matching import (
    NotificationCandidate,
    build_confirmed_message,
    build_escalation_message,
    is_final_step,
    pick_email_confirmation,
    schedule_next,
)
```

Y los métodos siguientes a la clase `BankEmailService`:

```python
    # ---------- Verificación ----------

    def request_verification(self, payment_id: int, *, now: datetime) -> dict:
        """
        Arranca (o devuelve) la verificación por correo de un pago entrante.

        Idempotente: reenviar dos veces la misma captura no consume un segundo correo.
        """
        payment = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.id == payment_id)
            .first()
        )
        if payment is None or payment.amount is None:
            return {"status": "skipped", "message": None}

        existing = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.incoming_payment_id == payment_id)
            .first()
        )
        if existing is not None and existing.status != BankEmailVerificationStatus.PENDING:
            return {
                "status": existing.status.value.lower(),
                "message": self._confirmed_message_for(existing, now=now),
            }

        amount = Decimal(str(payment.amount))
        verification = existing or BankEmailVerification(
            incoming_payment_id=payment_id,
            amount=amount,
            status=BankEmailVerificationStatus.PENDING,
            requested_at=now,
            escalation_step=0,
            next_notify_at=schedule_next(0, now),
        )
        if existing is None:
            self.db.add(verification)

        message = self._try_confirm(verification, payment, now=now)
        self.db.commit()

        if message is not None:
            return {"status": "confirmed", "message": message}
        return {"status": "pending", "message": None}

    def resolve_pending(self, *, now: datetime, notify: Callable[[str], bool]) -> list[str]:
        """
        Reevalúa las pendientes contra los correos ingeridos.

        El aviso se manda ANTES de commitear: si no sale, se revierte y la vuelta
        siguiente lo reintenta idéntico. Confirmar una verificación cuyo aviso nunca
        llegó es perder el producto entero de la feature.
        """
        delivered: list[str] = []
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.status == BankEmailVerificationStatus.PENDING)
            .all()
        )
        for verification in pendings:
            payment = (
                self.db.query(WhatsAppIncomingPayment)
                .filter(WhatsAppIncomingPayment.id == verification.incoming_payment_id)
                .first()
            )
            if payment is None:
                continue
            message = self._try_confirm(verification, payment, now=now)
            if message is None:
                continue
            if notify(message):
                self.db.commit()
                delivered.append(message)
            else:
                self.db.rollback()
        return delivered

    def escalate_pending(self, *, now: datetime, notify: Callable[[str], bool]) -> list[str]:
        """
        Manda el aviso del escalón que toque y avanza. Cierra en el último.

        Igual que `resolve_pending`: el escalón solo avanza si el aviso salió.
        """
        delivered: list[str] = []
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(
                BankEmailVerification.status == BankEmailVerificationStatus.PENDING,
                BankEmailVerification.next_notify_at.isnot(None),
                BankEmailVerification.next_notify_at <= now,
            )
            .all()
        )
        for verification in pendings:
            # El buzón caído congela: nunca se declara "no confirmado" sin haber podido mirar.
            if verification.frozen_until is not None and verification.frozen_until > now:
                continue

            payment = (
                self.db.query(WhatsAppIncomingPayment)
                .filter(WhatsAppIncomingPayment.id == verification.incoming_payment_id)
                .first()
            )
            phone = payment.client_phone if payment else "?"
            step = verification.escalation_step
            message = build_escalation_message(
                step, amount=verification.amount, client_phone=phone
            )
            if not notify(message):
                self.db.rollback()
                continue

            if is_final_step(step):
                verification.status = BankEmailVerificationStatus.NOT_FOUND
                verification.next_notify_at = None
                verification.resolved_at = now
            else:
                verification.escalation_step = step + 1
                verification.next_notify_at = schedule_next(step + 1, verification.requested_at)

            self.db.commit()
            delivered.append(message)

        return delivered

    def freeze_pending(self, *, now: datetime, minutes: int) -> int:
        """Congela la escalera de todas las pendientes. Devuelve cuántas congeló."""
        pendings = (
            self.db.query(BankEmailVerification)
            .filter(BankEmailVerification.status == BankEmailVerificationStatus.PENDING)
            .all()
        )
        for verification in pendings:
            verification.frozen_until = now + timedelta(minutes=minutes)
        if pendings:
            self.db.commit()
        return len(pendings)

    # ---------- Internos ----------

    def _try_confirm(
        self, verification: BankEmailVerification, payment: WhatsAppIncomingPayment, *, now: datetime
    ) -> Optional[str]:
        """Busca un correo que confirme el pago. Si lo hay, lo consume y devuelve el aviso."""
        rows = (
            self.db.query(BankEmailNotification)
            .filter(BankEmailNotification.consumed_by_payment_id.is_(None))
            .with_for_update()
            .all()
        )
        candidates = [
            NotificationCandidate(
                id=r.id,
                amount=r.amount,
                received_at=r.received_at,
                mailbox_label=r.mailbox_label,
                sender_name=r.sender_name or "",
                bank=r.bank,
            )
            for r in rows
        ]
        created_at = payment.created_at or verification.requested_at
        chosen, count = pick_email_confirmation(
            candidates,
            amount=verification.amount,
            payment_created_at=created_at,
            now=now,
        )
        if chosen is None:
            return None

        row = next(r for r in rows if r.id == chosen.id)
        row.consumed_by_payment_id = payment.id

        verification.status = BankEmailVerificationStatus.CONFIRMED
        verification.matched_notification_id = chosen.id
        verification.next_notify_at = None
        verification.resolved_at = now

        elapsed = int((now - verification.requested_at).total_seconds() // 60)
        return build_confirmed_message(
            chosen, amount=verification.amount, minutes_elapsed=elapsed, ambiguity_count=count
        )

    def _confirmed_message_for(
        self, verification: BankEmailVerification, *, now: datetime
    ) -> Optional[str]:
        """Rearma el aviso de una verificación ya confirmada, sin consumir nada."""
        if verification.matched_notification_id is None:
            return None
        row = (
            self.db.query(BankEmailNotification)
            .filter(BankEmailNotification.id == verification.matched_notification_id)
            .first()
        )
        if row is None:
            return None
        candidate = NotificationCandidate(
            id=row.id, amount=row.amount, received_at=row.received_at,
            mailbox_label=row.mailbox_label, sender_name=row.sender_name or "", bank=row.bank,
        )
        elapsed = int(((verification.resolved_at or now) - verification.requested_at).total_seconds() // 60)
        return build_confirmed_message(
            candidate, amount=verification.amount, minutes_elapsed=elapsed, ambiguity_count=1
        )
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_verification.py -v`
Expected: PASS (14 tests)

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/services/bank_email_service.py tests/test_bank_email_verification.py
git commit -m "feat: verify incoming Zelle payments against ingested bank emails"
```

---

## Task 9: Endpoint del bot

**Files:**
- Modify: `app/routers/whatsapp.py`
- Modify: `app/schemas/whatsapp.py`
- Test: `tests/test_bank_email_endpoint.py`

**Interfaces:**
- Consumes: `BankEmailService.request_verification` (Task 8).
- Produces: `POST /whatsapp/payments/incoming/{payment_id}/verify-by-email` → `EmailVerificationResponse(status: str, message: Optional[str])`

- [ ] **Step 1: Escribir el test que falla**

Crear `tests/test_bank_email_endpoint.py`:

```python
"""
Endpoint que el bot llama al reenviar una captura al grupo
(POST /whatsapp/payments/incoming/{id}/verify-by-email).

Se prueba el servicio a través del schema de respuesta; la autenticación del bot ya está
cubierta por el resto de la suite.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models.bank_email import BankEmailNotification
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.schemas.whatsapp import EmailVerificationResponse
from app.services.bank_email_service import BankEmailService

NOW = datetime(2026, 8, 4, 18, 30, tzinfo=timezone.utc)


def test_respuesta_confirmada_serializa(db):
    payment = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=30.0,
        currency="ZELLE", raw_text="x", created_at=NOW - timedelta(minutes=5),
    )
    db.add(payment)
    db.add(BankEmailNotification(
        message_id="<m1@boa>", mailbox_label="Jean", mailbox_email="azocarjean98@gmail.com",
        bank="BANK_OF_AMERICA", sender_name="Carlos R Barrientos", amount=Decimal("30.00"),
        currency="USD", received_at=NOW - timedelta(minutes=3), subject="s",
    ))
    db.commit()
    db.refresh(payment)

    result = BankEmailService(db).request_verification(payment.id, now=NOW)
    response = EmailVerificationResponse(**result)

    assert response.status == "confirmed"
    assert "Jean" in response.message


def test_respuesta_pendiente_serializa(db):
    payment = WhatsAppIncomingPayment(
        client_phone="584121234567", provider="zelle", amount=77.0,
        currency="ZELLE", raw_text="x", created_at=NOW,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)

    response = EmailVerificationResponse(**BankEmailService(db).request_verification(payment.id, now=NOW))

    assert response.status == "pending"
    assert response.message is None
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && python -m pytest tests/test_bank_email_endpoint.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmailVerificationResponse'`

- [ ] **Step 3: Agregar el schema**

En `app/schemas/whatsapp.py`, al final:

```python
class EmailVerificationResponse(BaseModel):
    """
    Resultado de verificar un pago entrante contra los correos de los bancos.

    `confirmed` trae el texto listo para que el bot lo pegue en el mensaje al operador;
    `pending` significa que se sigue buscando en segundo plano; `skipped`, que el pago no
    era verificable (sin monto).
    """

    status: str  # "confirmed" | "pending" | "skipped"
    message: Optional[str] = None
```

- [ ] **Step 4: Agregar el endpoint**

En `app/routers/whatsapp.py`, junto a los otros endpoints de `payments/incoming` (después de `match_forwarded_incoming`):

```python
@router.post(
    "/payments/incoming/{payment_id}/verify-by-email",
    response_model=EmailVerificationResponse,
)
def verify_incoming_by_email(
    payment_id: int,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """
    ¿Llegó al correo del banco un pago del mismo monto que este comprobante?

    Lo llama el bot cuando el operador reenvía una captura de Zelle al grupo. Si el correo
    ya estaba, responde `confirmed` con el texto para el operador; si no, deja la
    verificación pendiente y el poller avisa cuando aparezca (o cuando venza).
    """
    result = BankEmailService(db).request_verification(
        payment_id, now=datetime.now(timezone.utc)
    )
    return EmailVerificationResponse(**result)
```

Agregar los imports que falten al principio de `app/routers/whatsapp.py`:

```python
from datetime import datetime, timezone

from app.schemas.whatsapp import EmailVerificationResponse
from app.services.bank_email_service import BankEmailService
```

(Si `datetime`/`timezone` ya están importados, no duplicar.)

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `cd backend && python -m pytest tests/test_bank_email_endpoint.py -v`
Expected: PASS (2 tests)

Verificar que la app levanta y el endpoint aparece:
Run: `cd backend && python -c "from app.main import app; print([r.path for r in app.routes if 'verify-by-email' in r.path])"`
Expected: `['/whatsapp/payments/incoming/{payment_id}/verify-by-email']`

- [ ] **Step 6: Commit**

```bash
cd backend
git add app/routers/whatsapp.py app/schemas/whatsapp.py tests/test_bank_email_endpoint.py
git commit -m "feat: add endpoint to verify an incoming payment against bank emails"
```

---

## Task 10: Tarea Celery y CLI de diagnóstico

**Files:**
- Create: `app/tasks/bank_email_tasks.py`
- Create: `app/cli/check_mailboxes.py`
- Modify: `app/celery_app.py`

**Interfaces:**
- Consumes: todo lo anterior.
- Produces: tarea Celery `app.tasks.bank_email_tasks.poll_bank_emails`.

- [ ] **Step 1: Crear la tarea**

Crear `app/tasks/bank_email_tasks.py`:

```python
"""
Poller de los buzones de las cuentas alquiladas. Se programa desde app/celery_app.py.

Cada vuelta hace tres cosas en orden: ingerir correos nuevos, resolver las verificaciones
pendientes contra lo ingerido, y escalar las que siguen sin confirmar.

Un lock en Redis evita que dos vueltas solapadas consuman el mismo correo dos veces.
"""

import asyncio
from datetime import datetime, timezone

from redis import Redis

from app.celery_app import celery_app
from app.core.config import settings
from app.database.connection import SessionLocal
from app.services.bank_email_imap import MailboxUnavailable
from app.services.bank_email_service import BankEmailService
from app.services.bot_notifier import notify_operator

LOCK_KEY = "bank_email_poll_lock"
LOCK_TTL_SECONDS = 55

#: Cuánto se congela la escalera cuando un buzón no se pudo leer. Un poco más que el
#: intervalo del poller, para que se descongele sola en cuanto el buzón vuelva.
FREEZE_MINUTES = 5

#: Un correo rechazado sigue en la bandeja y se relee cada vuelta. Sin esto avisaría cada
#: 60 s durante 24 h. Un día de silencio por correo es suficiente.
WARNED_TTL_SECONDS = 86400

#: Igual para el buzón caído: un aviso por hora alcanza para enterarse.
MAILBOX_ALERT_TTL_SECONDS = 3600


@celery_app.task(name="app.tasks.bank_email_tasks.poll_bank_emails")
def poll_bank_emails():
    boxes = settings.mailboxes_computed
    if not boxes:
        return "sin buzones configurados"

    redis = Redis.from_url(settings.REDIS_URL)
    if not redis.set(LOCK_KEY, "1", nx=True, ex=LOCK_TTL_SECONDS):
        return "otra vuelta en curso"

    db = SessionLocal()
    sent = 0
    ingested = 0
    try:
        service = BankEmailService(db)
        now = datetime.now(timezone.utc)

        any_failure = False
        for box in boxes:
            try:
                count, rejected = service.ingest_mailbox(box)
                ingested += count
                for item in rejected:
                    # Una sola vez por correo, aunque siga en la bandeja mañana.
                    if redis.set(f"bank_email_warned:{item.message_id}", "1",
                                 nx=True, ex=WARNED_TTL_SECONDS):
                        if _notify(item.text):
                            sent += 1
            except MailboxUnavailable as e:
                any_failure = True
                print(f"⚠️ {e}")
                if redis.set(f"bank_email_mailbox_down:{box.label}", "1",
                             nx=True, ex=MAILBOX_ALERT_TTL_SECONDS):
                    if _notify(f"🚨 No puedo leer el correo de {box.label} — revisar credenciales"):
                        sent += 1

        if any_failure:
            # Sin poder mirar, no se acusa a ningún pago de no existir.
            service.freeze_pending(now=now, minutes=FREEZE_MINUTES)

        sent += len(service.resolve_pending(now=now, notify=_notify))
        sent += len(service.escalate_pending(now=now, notify=_notify))

        return f"ingeridos={ingested} avisos={sent}"
    finally:
        db.close()
        try:
            redis.delete(LOCK_KEY)
        except Exception:
            pass


def _notify(text: str) -> bool:
    """notify_operator es async; el worker de Celery es síncrono."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(notify_operator(text))
    finally:
        loop.close()
```

- [ ] **Step 2: Programar la tarea**

En `app/celery_app.py`, agregar al `include`:

```python
        "app.tasks.bank_email_tasks",
```

Y al `beat_schedule`:

```python
    "poll-bank-emails-every-minute": {
        "task": "app.tasks.bank_email_tasks.poll_bank_emails",
        "schedule": 60.0,
    },
```

- [ ] **Step 3: Verificar que Celery registra la tarea**

Run: `cd backend && python -c "from app.celery_app import celery_app; import app.tasks.bank_email_tasks; print('app.tasks.bank_email_tasks.poll_bank_emails' in celery_app.tasks)"`
Expected: `True`

- [ ] **Step 4: Crear el CLI de diagnóstico**

Crear `app/cli/check_mailboxes.py`:

```python
"""
Diagnóstico manual de los buzones: `python -m app.cli.check_mailboxes`.

Es lo que se corre al agregar una cuenta nueva o cuando llega el aviso
"No puedo leer el correo de X". No escribe nada en la BD.
"""

from app.core.config import settings
from app.services.bank_email_imap import MailboxUnavailable, fetch_recent_headers
from app.services.bank_email_parsers import (
    authentication_ok,
    find_template,
    parse_bank_email,
)


def main() -> None:
    boxes = settings.mailboxes_computed
    if not boxes:
        print("❌ ZELLE_MAILBOXES vacío: la confirmación por correo está apagada.")
        return

    for box in boxes:
        print(f"\n=== {box.label} <{box.email}> ===")
        try:
            headers = fetch_recent_headers(box)
        except MailboxUnavailable as e:
            print(f"❌ {e}")
            continue

        print(f"✅ Conectado. {len(headers)} correos en las últimas 24 h.")
        found = 0
        for raw in headers:
            parsed = parse_bank_email(raw, box.label)
            if parsed is None:
                continue
            found += 1
            template = find_template(raw.from_addr)
            ok = template is not None and authentication_ok(raw.auth_results, template.auth_domain)
            mark = "✅" if ok else "🚫 (falla autenticación)"
            print(
                f"  {mark} {parsed.received_at:%d/%m %H:%M}  ${parsed.amount}  "
                f"{parsed.sender_name}  [{parsed.bank}]"
            )
        if found == 0:
            print("  (ninguna notificación de pago reconocida en las últimas 24 h)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Probar el CLI contra Gmail de verdad**

Con `ZELLE_MAILBOXES` cargado en `.env`:

Run: `cd backend && python -m app.cli.check_mailboxes`
Expected: por cada buzón, "✅ Conectado" y la lista de notificaciones reconocidas de las últimas 24 h.

Si sale `AUTHENTICATIONFAILED`: la contraseña de aplicación es inválida o la cuenta no
tiene 2FA activo. Si conecta pero no reconoce ninguna notificación habiendo pagos
recientes, copiar el asunto real y agregar el caso a `tests/test_bank_email_parsers.py`
antes de tocar el regex.

- [ ] **Step 6: Correr toda la suite**

Run: `cd backend && python -m pytest tests/ -q`
Expected: todo en verde.

- [ ] **Step 7: Commit**

```bash
cd backend
git add app/tasks/bank_email_tasks.py app/cli/check_mailboxes.py app/celery_app.py
git commit -m "feat: poll mailboxes on a schedule and add mailbox diagnostics CLI"
```

---

## Task 11: Lado del bot

**Files:**
- Modify: `whatsapp-bot/src/api-client.ts`
- Modify: `whatsapp-bot/src/op-bridge.ts`
- Modify: `whatsapp-bot/src/whatsapp.ts:1037-1059`

**Interfaces:**
- Consumes: `POST /whatsapp/payments/incoming/{id}/verify-by-email` (Task 9).
- Produces: `bridgeVerifyIncomingByEmail(incomingId: number): Promise<string | null>` — devuelve la línea a mostrarle al operador, o `null`.

**Ojo:** este task es en el repo `whatsapp-bot/`, que es un git repo distinto del backend
y tiene su propia rama.

- [ ] **Step 1: Crear la rama en el repo del bot**

```bash
cd whatsapp-bot
git status --short          # tiene que estar limpio antes de empezar
git checkout -b feat/zelle-email-confirmation
```

- [ ] **Step 2: Agregar el cliente HTTP**

En `whatsapp-bot/src/api-client.ts`, junto a `apiMarkIncomingForwardedToGroup`:

```typescript
export interface ApiEmailVerification {
  // 'not_found' llega si se reenvía una captura cuya verificación ya venció.
  status: 'confirmed' | 'pending' | 'skipped' | 'not_found';
  message: string | null;
}

/** ¿Llegó al correo del banco un pago del mismo monto que este comprobante? */
export async function apiVerifyIncomingByEmail(id: number): Promise<ApiEmailVerification> {
  return request<ApiEmailVerification>({
    method: 'POST',
    url: `/whatsapp/payments/incoming/${id}/verify-by-email`,
  });
}
```

- [ ] **Step 3: Agregar el puente**

En `whatsapp-bot/src/op-bridge.ts`, junto a `bridgeMarkIncomingForwardedToGroup`:

```typescript
/**
 * Verificación del Zelle contra los correos de los bancos. Devuelve la línea para el
 * mensaje al operador, o null si no hay nada que decir.
 *
 * Nunca lanza: el reenvío al grupo ya se contabilizó y no puede fallar porque el
 * backend de correos esté caído.
 */
export async function bridgeVerifyIncomingByEmail(incomingId: number): Promise<string | null> {
  if (!isBackendEnabled()) return null;
  try {
    const result = await apiVerifyIncomingByEmail(incomingId);
    if (result.status === 'confirmed') return result.message;
    if (result.status === 'pending') return '⏳ Buscando confirmación en los correos…';
    return null;
  } catch {
    return null;
  }
}
```

Agregar `apiVerifyIncomingByEmail` al import de `./api-client` que ya existe en ese archivo.

- [ ] **Step 4: Llamarlo al reenviar al grupo**

En `whatsapp-bot/src/whatsapp.ts`, dentro de `if (forwardedSource) { … }`, agregar la
llamada después de `bridgeMarkOperationScenario` y la línea antes del `sendToPhone`:

```typescript
      const emailLine = await bridgeVerifyIncomingByEmail(forwardedSource.id);

      const lines: string[] = [`🔁 *Reenvío Zelle al grupo detectado* — ${clientPhone}`];
      if (fields.amount != null) lines.push(`💰 ${fields.amount.toLocaleString('es-VE', { minimumFractionDigits: 2 })} ZELLE`);
      if (fields.reference)      lines.push(`🔖 Ref: ${fields.reference}`);
      lines.push(`🔗 Comprobante original de \`${forwardedSource.client_phone}\` (incoming #${forwardedSource.id})`);
      lines.push(`📒 Entrante contabilizado en el grupo (sin saliente duplicado)`);
      if (taggedOp) lines.push(`🏷️ Op \`${taggedOp.id.substring(0, 8)}\` marcada *Zelle directo* (grupo)`);
      if (emailLine) lines.push(emailLine);
```

Agregar `bridgeVerifyIncomingByEmail` al import de `./op-bridge` que ya existe en ese archivo.

- [ ] **Step 5: Verificar que compila**

Run: `cd whatsapp-bot && npx tsc --noEmit`
Expected: sin errores.

- [ ] **Step 6: Correr los tests del bot**

Run: `cd whatsapp-bot && npx tsx src/test-ocr.ts && npx tsx src/test-routing.ts`
Expected: los casos existentes siguen pasando (este cambio no toca OCR ni routing, así que
cualquier fallo acá es una regresión).

- [ ] **Step 7: Commit**

```bash
cd whatsapp-bot
git add src/api-client.ts src/op-bridge.ts src/whatsapp.ts
git commit -m "feat: ask backend to confirm forwarded Zelle against bank emails"
```

---

## Task 12: Prueba de punta a punta en local

**Files:** ninguno (verificación manual).

- [ ] **Step 1: Levantar todo**

```bash
cd backend
docker-compose up -d db redis
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

En otras terminales:
```bash
cd backend && celery -A app.celery_app worker --loglevel=info
cd backend && celery -A app.celery_app beat --loglevel=info
```

Nota: el puerto 8000 suele estar tomado por `docker-proxy`; si `uvicorn` falla con
"address already in use", bajar el contenedor `backend` del compose antes.

- [ ] **Step 2: Verificar que el poller corre**

Mirar el log del worker durante dos minutos.
Expected: una línea por minuto con `ingeridos=N avisos=M`. Si dice "sin buzones
configurados", falta `ZELLE_MAILBOXES` en el `.env` del backend.

- [ ] **Step 3: Caso confirmado**

1. Hacerse un Zelle real (o pedir uno) de un monto poco común a una de las cuentas.
2. Esperar a que el poller lo ingiera:
   ```bash
   psql postgresql://tasas_user:tasas_password@localhost:5433/tasas_db \
     -c "SELECT mailbox_label, sender_name, amount, received_at FROM bank_email_notifications ORDER BY id DESC LIMIT 5"
   ```
3. Mandarle al bot la captura de ese Zelle como cliente, y reenviarla al grupo.

Expected: el mensaje al operador incluye `✅ *Pago confirmado por correo*` con el nombre
del remitente y la cuenta correcta.

- [ ] **Step 4: Caso escalera**

Reenviar al grupo una captura de un monto que NO exista en ningún correo.

Expected: el mensaje trae `⏳ Buscando confirmación en los correos…`, y a los 5 minutos
llega el primer recordatorio. Verificar el estado:
```bash
psql postgresql://tasas_user:tasas_password@localhost:5433/tasas_db \
  -c "SELECT incoming_payment_id, status, escalation_step, next_notify_at FROM bank_email_verifications ORDER BY id DESC LIMIT 5"
```
Expected: `status=PENDING`, `escalation_step=1` después del primer aviso.

- [ ] **Step 5: Caso buzón caído**

Cambiar una contraseña de `ZELLE_MAILBOXES` por una inválida y reiniciar el worker.

Expected: llega `🚨 No puedo leer el correo de <cuenta> — revisar credenciales`, y las
verificaciones pendientes **no** avanzan de escalón (`frozen_until` con fecha futura).
Restaurar la contraseña buena al terminar.

- [ ] **Step 6: Commit final y push**

```bash
cd backend && git push -u origin feat/zelle-email-confirmation
cd ../whatsapp-bot && git push -u origin feat/zelle-email-confirmation
```

---

## Notas de despliegue

- La migración corre sola con `alembic upgrade head` en el contenedor `backend` de prod.
- `ZELLE_MAILBOXES` va en el `.env` de prod (EC2). Sin esa variable la feature queda
  apagada y nada cambia: buen orden de despliegue es subir el código primero y cargar los
  buzones después, ya con `check_mailboxes` verificando.
- El `beat_schedule` nuevo lo toma el contenedor `scheduler`; hay que reiniciarlo.
- Las contraseñas de aplicación de Gmail requieren 2FA activo en cada cuenta.
