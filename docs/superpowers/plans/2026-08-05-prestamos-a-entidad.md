# Préstamos a una entidad — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Poder registrar préstamos a un negocio que no tiene teléfono propio en el bot (los comprobantes se mandan al grupo del negocio), incluyendo préstamos sin comprobante, y ver el total que ese negocio debe.

**Architecture:** El negocio se representa como un `WhatsAppClient` con clave sintética `entity:{slug}` — sin tabla nueva, reusando ficha, historial y saldo. El bloqueo por JID de grupo en el alta de préstamo se cambia por un deudor explícito (`client_uuid`), preseleccionado a partir de un `linked_group_jid` nuevo en el cliente. `client_loans.outgoing_payment_id` pasa a nullable para admitir el alta manual, que valora con las mismas funciones históricas que ya usa el alta desde comprobante.

**Tech Stack:** Backend FastAPI + SQLAlchemy + Alembic + pytest (integración contra Postgres real en :5433). Frontend Next.js (App Router) + TypeScript + Tailwind + vitest.

**Spec:** `backend/docs/superpowers/specs/2026-08-05-prestamos-a-entidad-design.md`

## Global Constraints

- **Dos repos git separados:** `backend/` y `frontend/`. Cada commit va en su repo; nunca se mezclan cambios de ambos en un commit.
- **Mensajes de commit en inglés** (`backend/CLAUDE.md`). Los comentarios de código y los textos de UI van en español, como el resto del código.
- **Routers nunca tocan modelos directamente:** siempre vía servicio o repositorio (`backend/CLAUDE.md`).
- **Errores de negocio:** `QuoteServiceError(code, message, http_status)` en el servicio; el router lo mapea a `HTTPException`.
- **Los tests de backend se saltan solos** si no hay Postgres en `:5433`. Levantarlo antes de correrlos: `docker-compose up -d db`.
- **Comandos desde `backend/`:** `pytest tests/test_client_loans.py -v`. Desde `frontend/`: `npm run test`, `npm run build`.
- **Prefijos de teléfono sintético ya en uso:** `anon:group:{id}`, `anon:partner:{user_id}`. El nuevo es `entity:{slug}` y **no** debe entrar en `is_unassigned_client_phone`.
- **Revisión Alembic actual (head):** `31ac3d9074b1` (`alembic/versions/31ac3d9074b1_add_bank_email_tables.py`).
- Los tests de integración crean el esquema con `Base.metadata.create_all`, **no** corren migraciones: todo cambio de columna debe estar en el modelo *y* en la migración.

---

### Task 1: Migración y modelo — entidad vinculada a un grupo, préstamo sin comprobante

**Files:**
- Create: `backend/alembic/versions/18e341e018c3_entity_clients_and_manual_loans.py`
- Modify: `backend/app/models/whatsapp_client.py:22-44` (columnas) y `:57-77` (`dict()`)
- Modify: `backend/app/models/client_loan.py:36-42`
- Test: `backend/tests/test_client_loans.py`

**Interfaces:**
- Consumes: nada (primera tarea).
- Produces: `WhatsAppClient.linked_group_jid: Optional[str]` (expuesto en `WhatsAppClient.dict()` como `linked_group_jid`); `ClientLoan.outgoing_payment_id: Optional[int]`.

- [x] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_client_loans.py`:

```python
"""Préstamos a entidades: deudor explícito, alta manual y totales."""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.client_loan import ClientLoan, ClientLoanPreferredValue, ClientLoanStatus
from app.models.whatsapp_client import WhatsAppClient


def test_loan_without_outgoing_payment_persists(db, client):
    """Un préstamo sin comprobante es válido: `outgoing_payment_id` queda vacío."""
    loan = ClientLoan(
        client_id=client.id,
        outgoing_payment_id=None,
        fiat_amount=1000,
        fiat_currency="VES",
        usdt_amount=10,
        usdt_rate=100,
        bcv_amount=8,
        bcv_rate=125,
        valuation_at=datetime.now(timezone.utc),
        preferred_value=ClientLoanPreferredValue.BCV,
        status=ClientLoanStatus.OPEN,
    )
    db.add(loan)
    db.flush()

    assert loan.id is not None
    assert loan.outgoing_payment_id is None


def test_client_stores_linked_group_jid(db):
    """El cliente-entidad guarda el JID del grupo por el que llegan sus comprobantes."""
    entity = WhatsAppClient(
        phone="entity:bodegon-x",
        display_name="Bodegón X",
        linked_group_jid="120363000000000000@g.us",
    )
    db.add(entity)
    db.flush()

    assert entity.dict()["linked_group_jid"] == "120363000000000000@g.us"
```

- [x] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: FAIL — `TypeError: 'linked_group_jid' is an invalid keyword argument for WhatsAppClient` y `IntegrityError: null value in column "outgoing_payment_id"`.

- [x] **Step 3: Agregar la columna al modelo de cliente**

En `backend/app/models/whatsapp_client.py`, después de `default_payment_currency` (línea 39):

```python
    # Grupo de WhatsApp por el que llegan los comprobantes de esta entidad. Solo lo usan
    # los clientes-entidad (`entity:{slug}`): sirve para proponer el deudor cuando un pago
    # saliente se mandó al grupo del negocio en vez de a un teléfono.
    linked_group_jid = Column(String(64), nullable=True, unique=True, index=True)
```

Y en `dict()`, junto a `default_payment_currency`:

```python
            "linked_group_jid": self.linked_group_jid,
```

- [x] **Step 4: Aflojar `outgoing_payment_id`**

En `backend/app/models/client_loan.py`, líneas 36-42:

```python
    # Vacío cuando el préstamo se dio de alta a mano, sin comprobante que lo respalde.
    outgoing_payment_id = Column(
        Integer,
        ForeignKey("whatsapp_outgoing_payments.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
        index=True,
    )
```

- [x] **Step 5: Escribir la migración**

Crear `backend/alembic/versions/18e341e018c3_entity_clients_and_manual_loans.py`:

```python
"""entity clients and manual loans

Revision ID: 18e341e018c3
Revises: 31ac3d9074b1
Create Date: 2026-08-05
"""

from alembic import op
import sqlalchemy as sa


revision = "18e341e018c3"
down_revision = "31ac3d9074b1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("whatsapp_clients", sa.Column("linked_group_jid", sa.String(64), nullable=True))
    op.create_index(
        "ix_whatsapp_clients_linked_group_jid",
        "whatsapp_clients",
        ["linked_group_jid"],
        unique=True,
    )
    # Los préstamos dados de alta a mano no tienen comprobante. El UNIQUE se conserva:
    # en Postgres los NULL no chocan entre sí, así que sigue impidiendo dos préstamos
    # sobre el mismo pago saliente.
    op.alter_column("client_loans", "outgoing_payment_id", existing_type=sa.Integer(), nullable=True)


def downgrade():
    op.execute("DELETE FROM client_loans WHERE outgoing_payment_id IS NULL")
    op.alter_column("client_loans", "outgoing_payment_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_whatsapp_clients_linked_group_jid", table_name="whatsapp_clients")
    op.drop_column("whatsapp_clients", "linked_group_jid")
```

- [x] **Step 6: Correr los tests y verificar que pasan**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: PASS (2 tests).

- [x] **Step 7: Aplicar la migración en local y verificar**

Run:
```bash
cd backend && alembic upgrade head
psql postgresql://tasas_user:tasas_password@localhost:5433/tasas_db -c "\d client_loans" | grep outgoing_payment_id
psql postgresql://tasas_user:tasas_password@localhost:5433/tasas_db -c "\d whatsapp_clients" | grep linked_group_jid
```
Expected: `outgoing_payment_id` sin `not null`; `linked_group_jid` presente. Los préstamos existentes siguen ahí (`SELECT count(*) FROM client_loans;` no cambia).

- [x] **Step 8: Commit**

```bash
cd backend
git add app/models/whatsapp_client.py app/models/client_loan.py alembic/versions/18e341e018c3_entity_clients_and_manual_loans.py tests/test_client_loans.py
git commit -m "feat: allow entity clients and loans without a receipt"
```

---

### Task 2: Alta de clientes-entidad (servicio + `POST /clients`)

**Files:**
- Create: `backend/app/services/client_entity_service.py`
- Modify: `backend/app/schemas/client.py` (agregar `ClientCreate`, campo en `ClientResponse` y `ClientUpdate`)
- Modify: `backend/app/routers/clients.py` (nuevo endpoint + import)
- Test: `backend/tests/test_client_entities.py`

**Interfaces:**
- Consumes: `WhatsAppClient.linked_group_jid` (Task 1).
- Produces:
  - `app.services.client_entity_service.ENTITY_PHONE_PREFIX: str = "entity:"`
  - `is_entity_client_phone(phone: Optional[str]) -> bool`
  - `slugify(name: str) -> str`
  - `ClientEntityService(db).create(display_name: str, linked_group_jid: Optional[str]) -> WhatsAppClient`
  - `POST /clients` con body `{display_name, linked_group_jid?}` → `ClientResponse` (201)

- [x] **Step 1: Escribir el test que falla**

Crear `backend/tests/test_client_entities.py`:

```python
"""Alta de clientes-entidad: negocios sin teléfono propio en el bot."""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.services.client_entity_service import (
    ClientEntityService,
    is_entity_client_phone,
    slugify,
)
from app.services.whatsapp_quote_service import (
    QuoteServiceError,
    is_unassigned_client_phone,
)


def test_slug_strips_accents_and_symbols():
    assert slugify("Bodegón X, C.A.") == "bodegon-x-c-a"


def test_create_entity_builds_synthetic_phone(db):
    entity = ClientEntityService(db).create("Bodegón X")

    assert entity.phone == "entity:bodegon-x"
    assert entity.display_name == "Bodegón X"
    assert is_entity_client_phone(entity.phone) is True
    # Una entidad es un cliente de verdad, no un marcador de "no sabemos quién es".
    assert is_unassigned_client_phone(entity.phone) is False


def test_second_entity_with_same_name_gets_a_suffix(db):
    service = ClientEntityService(db)
    service.create("Bodegón X")
    second = service.create("Bodegón X")

    assert second.phone == "entity:bodegon-x-2"


def test_entity_links_a_group_jid(db):
    entity = ClientEntityService(db).create("Bodegón X", "120363000000000000@g.us")

    assert entity.linked_group_jid == "120363000000000000@g.us"


def test_group_cannot_be_linked_to_two_entities(db):
    service = ClientEntityService(db)
    service.create("Bodegón X", "120363000000000000@g.us")

    with pytest.raises(QuoteServiceError) as exc:
        service.create("Otro negocio", "120363000000000000@g.us")

    assert exc.value.code == "group_already_linked"
    assert exc.value.http_status == 409


def test_entity_name_is_required(db):
    with pytest.raises(QuoteServiceError) as exc:
        ClientEntityService(db).create("   ")

    assert exc.value.code == "invalid_entity_name"
```

- [x] **Step 2: Correr el test y verificar que falla**

Run: `cd backend && pytest tests/test_client_entities.py -v`
Expected: FAIL con `ModuleNotFoundError: No module named 'app.services.client_entity_service'`.

- [x] **Step 3: Escribir el servicio**

Crear `backend/app/services/client_entity_service.py`:

```python
"""
Clientes-entidad: negocios a los que el operador les presta o cobra pero que no tienen
teléfono propio en el bot (sus comprobantes se mandan al grupo del negocio).

Se guardan en `whatsapp_clients` con una clave sintética `entity:{slug}`, igual que los
anónimos usan `anon:group:{id}` / `anon:partner:{user_id}`. La diferencia: un `anon:` es un
marcador de "todavía no sabemos quién es" y varios flujos lo excluyen; una entidad es un
cliente de verdad, con ficha, historial y saldo.
"""

import re
import unicodedata
from typing import Optional

from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.services.whatsapp_quote_service import QuoteServiceError

ENTITY_PHONE_PREFIX = "entity:"


def is_entity_client_phone(phone: Optional[str]) -> bool:
    return bool(phone) and phone.startswith(ENTITY_PHONE_PREFIX)


def slugify(name: str) -> str:
    """«Bodegón X, C.A.» → «bodegon-x-c-a»."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-")


class ClientEntityService:
    def __init__(self, db: Session):
        self.db = db

    def create(self, display_name: str, linked_group_jid: Optional[str] = None) -> WhatsAppClient:
        name = (display_name or "").strip()
        if not name:
            raise QuoteServiceError(
                "invalid_entity_name", "El nombre de la entidad es obligatorio", 400
            )
        slug = slugify(name)
        if not slug:
            raise QuoteServiceError(
                "invalid_entity_name", "El nombre debe tener al menos una letra o un número", 400
            )

        jid = (linked_group_jid or "").strip() or None
        if jid is not None:
            taken = (
                self.db.query(WhatsAppClient)
                .filter(WhatsAppClient.linked_group_jid == jid)
                .first()
            )
            if taken is not None:
                raise QuoteServiceError(
                    "group_already_linked",
                    f"Ese grupo ya está vinculado a «{taken.display_name}»",
                    409,
                )

        entity = WhatsAppClient(
            phone=self._free_phone(slug), display_name=name, linked_group_jid=jid
        )
        self.db.add(entity)
        self.db.commit()
        self.db.refresh(entity)
        return entity

    def _free_phone(self, slug: str) -> str:
        """Dos negocios pueden llamarse igual; el segundo lleva sufijo."""
        phone = f"{ENTITY_PHONE_PREFIX}{slug}"
        suffix = 2
        while (
            self.db.query(WhatsAppClient.id).filter(WhatsAppClient.phone == phone).first()
            is not None
        ):
            phone = f"{ENTITY_PHONE_PREFIX}{slug}-{suffix}"
            suffix += 1
        return phone
```

- [x] **Step 4: Correr los tests y verificar que pasan**

Run: `cd backend && pytest tests/test_client_entities.py -v`
Expected: PASS (6 tests).

- [x] **Step 5: Exponer el campo y el alta en los schemas**

En `backend/app/schemas/client.py`, dentro de `ClientResponse` (después de `default_payment_currency`):

```python
    # Grupo de WhatsApp vinculado; solo lo llevan los clientes-entidad.
    linked_group_jid: Optional[str] = None
```

Dentro de `ClientUpdate`, al final:

```python
    # Grupo vinculado de una entidad; enviar null para desvincular.
    linked_group_jid: Optional[str] = None
```

Y una clase nueva al final del archivo:

```python
class ClientCreate(BaseModel):
    """
    Alta manual de un cliente-entidad (un negocio sin teléfono en el bot). Los clientes
    normales no se crean por aquí: nacen del tráfico del bot.
    """
    display_name: str
    linked_group_jid: Optional[str] = None
```

- [x] **Step 6: Escribir el endpoint**

En `backend/app/routers/clients.py`, agregar el import:

```python
from app.schemas.client import ClientCreate, ClientList, ClientResponse, ClientUpdate
from app.services.client_entity_service import ClientEntityService
```

Y el endpoint, justo antes de `@router.get("", response_model=ClientList)`:

```python
@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_entity_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    """Da de alta un negocio sin teléfono propio como cliente-entidad."""
    try:
        entity = ClientEntityService(db).create(payload.display_name, payload.linked_group_jid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return ClientResponse(**entity.dict(), balance=0.0)
```

- [x] **Step 7: Verificar el endpoint a mano**

Run: `cd backend && uvicorn app.main:app --port 8000 --reload` y en otra terminal:
```bash
curl -s -X POST localhost:8000/clients -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"display_name":"Bodegón X"}' | head -20
```
Expected: 201 con `"phone": "entity:bodegon-x"`. Repetir el mismo POST → segundo con `entity:bodegon-x-2`.

- [x] **Step 8: Commit**

```bash
cd backend
git add app/services/client_entity_service.py app/schemas/client.py app/routers/clients.py tests/test_client_entities.py
git commit -m "feat: create entity clients from the operator panel"
```

---

### Task 3: Deudor explícito en el préstamo desde comprobante

**Files:**
- Modify: `backend/app/services/client_loan_service.py:103-189` (`preview_outgoing`) y `:202-325` (`create_from_outgoing`)
- Modify: `backend/app/schemas/whatsapp.py:362-386` (`ClientLoanCreate`)
- Modify: `backend/app/routers/payments.py:60-81`
- Test: `backend/tests/test_client_loans.py`

**Interfaces:**
- Consumes: `is_entity_client_phone` no hace falta aquí; sí `is_unassigned_client_phone` de `app.services.whatsapp_quote_service`; `WhatsAppClient.linked_group_jid` (Task 1).
- Produces:
  - `ClientLoanService.create_from_outgoing(..., client_uuid: Optional[UUID] = None)`
  - `preview_outgoing()` devuelve además `"requires_borrower": bool` y `"suggested_client": {"uuid": str, "display_name": str} | None`
  - Códigos de error nuevos: `loan_borrower_required` (400), `loan_client_invalid` (400)

- [x] **Step 1: Escribir los tests que fallan**

Agregar a `backend/tests/test_client_loans.py` (los imports de arriba ya existen; agregar los que faltan):

```python
import app.services.bcv_service as bcv_service
from app.models.bcv_rate import BcvRate
from app.models.exchange_rate import ExchangeRate
from app.services.client_entity_service import ClientEntityService
from app.services.client_loan_service import ClientLoanService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories


@pytest.fixture
def ves_rates(db):
    """
    Tasas para valorar bolívares: VES/USDT del negocio y la oficial del BCV.

    Ojo con la dirección: el par va FIAT→USDT y `rate` son bolívares por USDT, con
    `inverse_percentage=True` para que aplicarlo sea dividir. Es la convención del sistema
    (ver `WhatsAppRateResolver._get_direct_entry` y `valuation.historical_rate`); guardarlo
    al revés hace que las dos rutas de conversión den resultados distintos.
    """
    # Fechadas en el pasado: la valuación histórica solo mira tasas anteriores al momento
    # del préstamo, y los tests valoran a "hace dos horas".
    past = datetime.now(timezone.utc) - timedelta(days=7)
    db.add(ExchangeRate(
        from_currency="VES", to_currency="USDT", rate=500.0,
        inverse_percentage=True, is_active=True, created_at=past,
    ))
    db.add(BcvRate(rate=400.0, fetched_at=past))
    db.flush()
    # `get_cached_bcv_rate` cachea en una global del módulo; sin limpiarla, la tasa de un
    # test se cuela en el siguiente.
    bcv_service._cached_rate = None
    bcv_service._cached_expiry = 0.0


@pytest.fixture
def entity(db):
    return ClientEntityService(db).create("Bodegón X", "120363000000000000@g.us")


def test_loan_from_group_payment_requires_a_borrower(db, ves_rates):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    with pytest.raises(QuoteServiceError) as exc:
        ClientLoanService(db).create_from_outgoing(
            payment_id=payment.id,
            preferred_value="BCV",
            payment_currency="VES",
            fiat_currency="VES",
        )

    assert exc.value.code == "loan_borrower_required"
    assert exc.value.http_status == 400


def test_loan_from_group_payment_uses_the_given_borrower(db, ves_rates, entity):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    loan = ClientLoanService(db).create_from_outgoing(
        payment_id=payment.id,
        preferred_value="BCV",
        payment_currency="VES",
        fiat_currency="VES",
        client_uuid=entity.uuid,
    )

    assert loan["client_uuid"] == entity.uuid
    assert loan["preferred_currency"] == "USD_BCV"
    assert loan["principal_amount"] == pytest.approx(12.5)  # 5000 VES / 400 BCV


def test_loan_from_phone_payment_still_works_without_borrower(db, ves_rates, client):
    payment = factories.outgoing(db, 5000, "VES", phone=client.phone)

    loan = ClientLoanService(db).create_from_outgoing(
        payment_id=payment.id,
        preferred_value="FIAT",
        payment_currency="VES",
        fiat_currency="VES",
    )

    assert loan["client_uuid"] == client.uuid


def test_anonymous_client_cannot_be_a_borrower(db, ves_rates, client):
    anon = WhatsAppClient(phone="anon:group:1", display_name="Anónimo (vía fondo)")
    db.add(anon)
    db.flush()
    payment = factories.outgoing(db, 5000, "VES", phone=client.phone)

    with pytest.raises(QuoteServiceError) as exc:
        ClientLoanService(db).create_from_outgoing(
            payment_id=payment.id,
            preferred_value="FIAT",
            payment_currency="VES",
            fiat_currency="VES",
            client_uuid=anon.uuid,
        )

    assert exc.value.code == "loan_client_invalid"


def test_preview_suggests_the_entity_linked_to_the_group(db, ves_rates, entity):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    preview = ClientLoanService(db).preview_outgoing(payment.id, "VES", "VES")

    assert preview["requires_borrower"] is True
    assert preview["suggested_client"] == {"uuid": entity.uuid, "display_name": "Bodegón X"}
```

- [x] **Step 2: Correr los tests y verificar que fallan**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: FAIL — el primero con `invalid_client` ("No se puede registrar un préstamo a un grupo") en vez de `loan_borrower_required`; los demás con `TypeError: create_from_outgoing() got an unexpected keyword argument 'client_uuid'` y `KeyError: 'requires_borrower'`.

- [x] **Step 3: Resolver el deudor en el servicio**

En `backend/app/services/client_loan_service.py`, agregar al import del quote service:

```python
from app.services.whatsapp_quote_service import QuoteServiceError, is_unassigned_client_phone
```

Agregar el método justo antes de `create_from_outgoing`:

```python
    def _resolve_borrower(self, payment, client_uuid: Optional[UUID]) -> WhatsAppClient:
        """
        A nombre de quién queda el préstamo. Cuando el comprobante se mandó al grupo del
        negocio (o el cliente todavía es anónimo) el teléfono no dice nada: el operador
        tiene que decir el deudor. Un `client_uuid` explícito manda siempre.
        """
        if client_uuid is not None:
            client = (
                self.db.query(WhatsAppClient)
                .filter(WhatsAppClient.uuid == str(client_uuid))
                .first()
            )
            if client is None:
                raise QuoteServiceError("client_not_found", "El deudor indicado no existe", 404)
        else:
            if is_unassigned_client_phone(payment.client_phone):
                raise QuoteServiceError(
                    "loan_borrower_required",
                    "Indica a nombre de quién queda el préstamo",
                    400,
                )
            client = (
                self.db.query(WhatsAppClient)
                .filter(WhatsAppClient.phone == payment.client_phone)
                .first()
            )
            if client is None:
                raise QuoteServiceError("client_not_found", "El pago no tiene un cliente válido", 404)

        if is_unassigned_client_phone(client.phone):
            raise QuoteServiceError(
                "loan_client_invalid",
                "Un cliente anónimo no puede ser el deudor de un préstamo",
                400,
            )
        return client
```

- [x] **Step 4: Cambiar el bloqueo por la resolución del deudor**

En `create_from_outgoing`, agregar el parámetro a la firma (después de `payment_id`):

```python
        client_uuid: Optional[UUID] = None,
```

Borrar el bloqueo de las líneas 221-222:

```python
        if payment.client_phone.endswith("@g.us"):
            raise QuoteServiceError("invalid_client", "No se puede registrar un préstamo a un grupo", 400)
```

Y reemplazar la búsqueda del cliente (líneas 303-305):

```python
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == payment.client_phone).first()
        if client is None:
            raise QuoteServiceError("client_not_found", "El pago no tiene un cliente válido", 404)
```

por:

```python
        client = self._resolve_borrower(payment, client_uuid)
```

**Importante:** mover esa línea al principio del método, justo después de comprobar que el pago existe y antes de calcular equivalencias — así un deudor inválido falla antes de consultar tasas.

- [x] **Step 5: Sugerir la entidad en el preview**

En `preview_outgoing`, antes del `return`:

```python
        requires_borrower = is_unassigned_client_phone(payment.client_phone)
        suggested_client = None
        if requires_borrower:
            entity = (
                self.db.query(WhatsAppClient)
                .filter(WhatsAppClient.linked_group_jid == payment.client_phone)
                .first()
            )
            if entity is not None:
                suggested_client = {"uuid": entity.uuid, "display_name": entity.display_name}
```

Y dos claves nuevas en el dict devuelto:

```python
            "requires_borrower": requires_borrower,
            "suggested_client": suggested_client,
```

- [x] **Step 6: Pasar el deudor desde el router y el schema**

En `backend/app/schemas/whatsapp.py`, dentro de `ClientLoanCreate` (después de `preferred_value`):

```python
    # Deudor explícito. Obligatorio cuando el comprobante se mandó a un grupo.
    client_uuid: Optional[UUID] = None
```

(el archivo ya importa `UUID`; si no, agregar `from uuid import UUID`).

En `backend/app/routers/payments.py`, dentro de `create_client_loan`, agregar el argumento a la llamada:

```python
            client_uuid=payload.client_uuid,
```

- [x] **Step 7: Correr los tests y verificar que pasan**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: PASS (7 tests).

- [x] **Step 8: Commit**

```bash
cd backend
git add app/services/client_loan_service.py app/schemas/whatsapp.py app/routers/payments.py tests/test_client_loans.py
git commit -m "feat: name the borrower when a loan receipt was sent to a group"
```

---

### Task 4: Alta manual de préstamos (sin comprobante)

**Files:**
- Modify: `backend/app/services/client_loan_service.py` (extraer `_persist_loan`, agregar `preview_manual` y `create_manual`)
- Modify: `backend/app/schemas/whatsapp.py` (agregar `ClientLoanManualCreate`)
- Modify: `backend/app/routers/clients.py` (dos endpoints nuevos)
- Test: `backend/tests/test_client_loans.py`

**Interfaces:**
- Consumes: `ClientLoanService._resolve_borrower` (Task 3); `app.services.valuation.equivalents(db, amount, currency, at) -> dict` (ya existe, devuelve `usdt_amount`, `usdt_rate`, `bcv_amount`, `bcv_rate`, `valuation_at`, `warnings`).
- Produces:
  - `ClientLoanService.preview_manual(client_uuid: UUID, amount: float, fiat_currency: str, at: datetime) -> dict`
  - `ClientLoanService.create_manual(client_uuid: UUID, preferred_value: str, fiat_currency: str, fiat_amount: float, valuation_at: datetime, usdt_amount: Optional[float], bcv_amount: Optional[float], notes: Optional[str], created_by_user_id: Optional[int]) -> dict`
  - `GET /clients/{uuid}/loans/valuation?amount=&currency=&at=`
  - `POST /clients/{uuid}/loans` (201)
  - Código de error nuevo: `invalid_valuation_date` (400)

- [x] **Step 1: Escribir los tests que fallan**

Agregar a `backend/tests/test_client_loans.py`:

```python
def test_manual_valuation_uses_historical_rates(db, ves_rates, entity):
    at = datetime.now(timezone.utc) - timedelta(hours=2)

    preview = ClientLoanService(db).preview_manual(entity.uuid, 5000, "VES", at)

    assert preview["usdt_amount"] == pytest.approx(10.0)   # 5000 / 500
    assert preview["bcv_amount"] == pytest.approx(12.5)    # 5000 / 400
    assert preview["warnings"] == []


def test_manual_loan_is_created_without_a_receipt(db, ves_rates, entity, operator):
    at = datetime.now(timezone.utc) - timedelta(hours=2)

    loan = ClientLoanService(db).create_manual(
        client_uuid=entity.uuid,
        preferred_value="BCV",
        fiat_currency="VES",
        fiat_amount=5000,
        valuation_at=at,
        notes="Factura de luz",
        created_by_user_id=operator.id,
    )

    assert loan["outgoing_payment_id"] is None
    assert loan["preferred_currency"] == "USD_BCV"
    assert loan["outstanding_amount"] == pytest.approx(12.5)
    assert loan["manual_values"] is False


def test_manual_loan_flags_corrected_equivalences(db, ves_rates, entity):
    at = datetime.now(timezone.utc) - timedelta(hours=2)

    loan = ClientLoanService(db).create_manual(
        client_uuid=entity.uuid,
        preferred_value="USDT",
        fiat_currency="VES",
        fiat_amount=5000,
        valuation_at=at,
        usdt_amount=9.0,  # el operador corrige el equivalente sugerido (10.0)
    )

    assert loan["manual_values"] is True
    assert loan["usdt_amount"] == pytest.approx(9.0)


def test_manual_loan_rejects_a_future_date(db, ves_rates, entity):
    at = datetime.now(timezone.utc) + timedelta(days=1)

    with pytest.raises(QuoteServiceError) as exc:
        ClientLoanService(db).create_manual(
            client_uuid=entity.uuid,
            preferred_value="FIAT",
            fiat_currency="VES",
            fiat_amount=5000,
            valuation_at=at,
        )

    assert exc.value.code == "invalid_valuation_date"


def test_repayment_closes_a_manual_bcv_loan(db, ves_rates, entity):
    service = ClientLoanService(db)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    loan = service.create_manual(
        client_uuid=entity.uuid,
        preferred_value="BCV",
        fiat_currency="VES",
        fiat_amount=5000,
        valuation_at=at,
    )

    after = service.add_repayment(UUID(loan["uuid"]), 12.5)

    assert after["outstanding_amount"] == pytest.approx(0.0)
    assert after["status"] == "PAID"
```

Agregar `from uuid import UUID` a los imports del test.

- [x] **Step 2: Correr los tests y verificar que fallan**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: FAIL con `AttributeError: 'ClientLoanService' object has no attribute 'preview_manual'`.

- [x] **Step 3: Extraer la validación y la persistencia compartidas**

En `backend/app/services/client_loan_service.py`, agregar dos métodos privados antes de `create_from_outgoing`:

```python
    def _validate_reference(self, preferred_value: str, fiat_currency: str) -> tuple[ClientLoanPreferredValue, str]:
        try:
            preferred = ClientLoanPreferredValue(preferred_value.upper())
        except ValueError:
            raise QuoteServiceError("invalid_preferred_value", "Referencia preferida inválida", 400)
        currency = fiat_currency.strip().upper()
        if not currency or currency == "USDT":
            raise QuoteServiceError("invalid_fiat_currency", "Selecciona una moneda fiat válida", 400)
        if preferred == ClientLoanPreferredValue.BCV and currency != "VES":
            raise QuoteServiceError(
                "bcv_requires_ves",
                "La referencia BCV solo está disponible cuando la moneda fiat es VES",
                400,
            )
        return preferred, currency

    def _persist_loan(
        self,
        *,
        client: WhatsAppClient,
        outgoing_payment_id: Optional[int],
        preferred: ClientLoanPreferredValue,
        fiat_currency: str,
        fiat_amount: Optional[float],
        usdt_amount: Optional[float],
        bcv_amount: Optional[float],
        valuation_at: datetime,
        manual_values: bool,
        notes: Optional[str],
        created_by_user_id: Optional[int],
    ) -> dict:
        """Guarda el préstamo con sus tres equivalencias. Compartido por las dos altas."""
        if fiat_amount is None or fiat_amount <= 0:
            raise QuoteServiceError("invalid_fiat_amount", "Indica un valor fiat válido", 400)
        if usdt_amount is None or usdt_amount <= 0:
            raise QuoteServiceError("invalid_usdt_amount", "Indica un valor USDT válido", 400)
        if fiat_currency == "VES" and (bcv_amount is None or bcv_amount <= 0):
            raise QuoteServiceError("invalid_bcv_amount", "Indica un valor BCV válido", 400)

        usdt_rate = float(fiat_amount) / float(usdt_amount)
        bcv_rate = (
            float(fiat_amount) / float(bcv_amount)
            if fiat_currency == "VES" and bcv_amount is not None
            else None
        )
        loan = ClientLoan(
            client_id=client.id,
            outgoing_payment_id=outgoing_payment_id,
            fiat_amount=_decimal(fiat_amount),
            fiat_currency=fiat_currency,
            usdt_amount=_decimal(usdt_amount),
            usdt_rate=_decimal(usdt_rate),
            bcv_amount=_decimal(bcv_amount) if bcv_amount is not None else None,
            bcv_rate=_decimal(bcv_rate) if bcv_rate is not None else None,
            valuation_at=valuation_at,
            manual_values=manual_values,
            preferred_value=preferred,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(loan)
        self.db.commit()
        self.db.refresh(loan)
        return self.serialize(loan)
```

Mover también la función `changed()` (hoy anidada en `create_from_outgoing`, líneas 286-293) a nivel de módulo, para que las dos altas la usen:

```python
def _changed(provided: Optional[float], suggested: Optional[float]) -> bool:
    """
    ¿El operador corrigió la sugerencia? El frontend trabaja con centavos; el redondeo
    automático a dos decimales no debe quedar auditado como una corrección manual.
    """
    if provided is None:
        return False
    if suggested is None:
        return True
    return abs(float(provided) - float(suggested)) > 0.005000001
```

Y reescribir el final de `create_from_outgoing` (desde el cálculo de `usdt_rate`, línea 279, hasta el `return`) para delegar:

```python
        manual_values = any(
            (
                _changed(fiat_amount, suggested_fiat),
                _changed(usdt_amount, suggested_usdt),
                _changed(bcv_amount, suggested_bcv),
            )
        )
        return self._persist_loan(
            client=client,
            outgoing_payment_id=payment.id,
            preferred=preferred,
            fiat_currency=fiat_currency,
            fiat_amount=fiat_amount,
            usdt_amount=usdt_amount,
            bcv_amount=bcv_amount,
            valuation_at=preview["valuation_at"],
            manual_values=manual_values,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
```

Y reemplazar el bloque de validación de `preferred`/`fiat_currency` (líneas 239-252) por:

```python
        preferred, fiat_currency = self._validate_reference(preferred_value, fiat_currency)
```

- [x] **Step 4: Correr los tests existentes para verificar que el refactor no rompió nada**

Run: `cd backend && pytest tests/test_client_loans.py -v -k "not manual"`
Expected: PASS (los 7 tests de las tareas 1 y 3).

- [x] **Step 5: Escribir el alta manual**

Agregar al final de `ClientLoanService`:

```python
    def _client_by_uuid(self, client_uuid: UUID) -> WhatsAppClient:
        client = (
            self.db.query(WhatsAppClient).filter(WhatsAppClient.uuid == str(client_uuid)).first()
        )
        if client is None:
            raise QuoteServiceError("client_not_found", "Cliente no encontrado", 404)
        if is_unassigned_client_phone(client.phone):
            raise QuoteServiceError(
                "loan_client_invalid",
                "Un cliente anónimo no puede ser el deudor de un préstamo",
                400,
            )
        return client

    @staticmethod
    def _check_valuation_date(at: datetime) -> datetime:
        moment = at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)
        if moment > datetime.now(timezone.utc):
            raise QuoteServiceError(
                "invalid_valuation_date", "La fecha del préstamo no puede ser futura", 400
            )
        return moment

    def preview_manual(
        self, client_uuid: UUID, amount: float, fiat_currency: str, at: datetime
    ) -> dict:
        """Equivalencias de un préstamo dado de alta a mano, con las tasas de esa fecha."""
        self._client_by_uuid(client_uuid)
        moment = self._check_valuation_date(at)
        currency = (fiat_currency or "").strip().upper()
        if not currency or currency == "USDT":
            raise QuoteServiceError("invalid_fiat_currency", "Selecciona una moneda fiat válida", 400)
        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_fiat_amount", "Indica un valor fiat válido", 400)

        eq = valuation.equivalents(self.db, float(amount), currency, moment)
        return {
            "fiat_amount": float(amount),
            "fiat_currency": currency,
            "usdt_amount": eq["usdt_amount"],
            "usdt_rate": eq["usdt_rate"],
            "bcv_amount": eq["bcv_amount"],
            "bcv_rate": eq["bcv_rate"],
            "valuation_at": moment,
            "warnings": eq["warnings"],
        }

    def create_manual(
        self,
        client_uuid: UUID,
        preferred_value: str,
        fiat_currency: str,
        fiat_amount: float,
        valuation_at: datetime,
        usdt_amount: Optional[float] = None,
        bcv_amount: Optional[float] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        """Préstamo sin comprobante: el operador pone monto, moneda y fecha."""
        client = self._client_by_uuid(client_uuid)
        preferred, currency = self._validate_reference(preferred_value, fiat_currency)
        moment = self._check_valuation_date(valuation_at)

        preview = self.preview_manual(client_uuid, fiat_amount, currency, moment)
        suggested_usdt = preview["usdt_amount"]
        suggested_bcv = preview["bcv_amount"]
        final_usdt = usdt_amount if usdt_amount is not None else suggested_usdt
        final_bcv = bcv_amount if bcv_amount is not None else suggested_bcv
        if currency != "VES":
            final_bcv = None

        manual_values = any(
            (_changed(usdt_amount, suggested_usdt), _changed(bcv_amount, suggested_bcv))
        )
        return self._persist_loan(
            client=client,
            outgoing_payment_id=None,
            preferred=preferred,
            fiat_currency=currency,
            fiat_amount=float(fiat_amount),
            usdt_amount=final_usdt,
            bcv_amount=final_bcv,
            valuation_at=moment,
            manual_values=manual_values,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
```

- [x] **Step 6: Correr los tests y verificar que pasan**

Run: `cd backend && pytest tests/test_client_loans.py -v`
Expected: PASS (12 tests).

- [x] **Step 7: Exponer los endpoints**

En `backend/app/schemas/whatsapp.py`, después de `ClientLoanCreate`:

```python
class ClientLoanManualCreate(BaseModel):
    """Préstamo dado de alta a mano, sin comprobante que lo respalde."""
    preferred_value: str = Field(..., description="FIAT | USDT | BCV")
    fiat_currency: str = Field(..., min_length=2, max_length=10)
    fiat_amount: float = Field(..., gt=0)
    valuation_at: datetime
    usdt_amount: Optional[float] = Field(None, gt=0)
    bcv_amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None

    @validator("preferred_value")
    def validate_preferred_value(cls, value: str) -> str:
        value = value.upper()
        if value not in {"FIAT", "USDT", "BCV"}:
            raise ValueError("preferred_value must be FIAT, USDT or BCV")
        return value

    @validator("fiat_currency")
    def normalize_fiat_currency(cls, value: str) -> str:
        return value.strip().upper()
```

En `backend/app/routers/clients.py`, agregar `ClientLoanManualCreate` al import de `app.schemas.whatsapp`, `from datetime import datetime` arriba, y los dos endpoints después de `get_client_loans`:

```python
@router.get("/{client_uuid}/loans/valuation")
async def preview_manual_loan_valuation(
    client_uuid: UUID,
    amount: float = Query(..., gt=0),
    currency: str = Query(..., min_length=2, max_length=10),
    at: datetime = Query(..., description="Fecha del préstamo (ISO 8601)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Equivalencias de un préstamo sin comprobante, con las tasas de la fecha indicada."""
    try:
        return ClientLoanService(db).preview_manual(client_uuid, amount, currency, at)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/loans", status_code=status.HTTP_201_CREATED)
async def create_manual_loan(
    client_uuid: UUID,
    payload: ClientLoanManualCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Registra un préstamo a mano, sin comprobante."""
    try:
        return ClientLoanService(db).create_manual(
            client_uuid=client_uuid,
            preferred_value=payload.preferred_value,
            fiat_currency=payload.fiat_currency,
            fiat_amount=payload.fiat_amount,
            valuation_at=payload.valuation_at,
            usdt_amount=payload.usdt_amount,
            bcv_amount=payload.bcv_amount,
            notes=payload.notes,
            created_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
```

**Ojo con el orden de rutas:** `/{client_uuid}/loans/valuation` debe declararse antes que cualquier ruta `/{client_uuid}/loans/{loan_uuid}/...` para que FastAPI no interprete `valuation` como un uuid. Ponerlo justo después de `get_client_loans` cumple.

- [x] **Step 8: Verificar los endpoints a mano**

Run:
```bash
curl -s -G localhost:8000/clients/$ENTITY_UUID/loans/valuation \
  -H "Authorization: Bearer $TOKEN" \
  --data-urlencode "amount=5000" --data-urlencode "currency=VES" \
  --data-urlencode "at=2026-08-04T10:00:00Z"
```
Expected: JSON con `usdt_amount`, `bcv_amount` y `warnings`.

- [x] **Step 9: Commit**

```bash
cd backend
git add app/services/client_loan_service.py app/schemas/whatsapp.py app/routers/clients.py tests/test_client_loans.py
git commit -m "feat: register loans without a receipt"
```

---

### Task 5: Total de la deuda del negocio

**Files:**
- Modify: `backend/app/services/client_loan_service.py:372-383` (`list_for_client`) + método nuevo `_totals`
- Test: `backend/tests/test_client_loans.py`

**Interfaces:**
- Consumes: `ClientLoanService._convert(amount, from, to) -> tuple[float, float]` (ya existe; lanza `QuoteServiceError` si falta tasa); `get_cached_bcv_rate(db)` (ya importado).
- Produces: `list_for_client()` devuelve `{"client_uuid", "loans", "totals": {"by_reference": [{"currency": str, "amount": float}], "usdt_total": float | None, "warnings": [str]}}`

- [x] **Step 1: Escribir los tests que fallan**

Agregar a `backend/tests/test_client_loans.py`:

```python
def test_totals_group_by_reference_and_sum_in_usdt(db, ves_rates, entity):
    service = ClientLoanService(db)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    service.create_manual(
        client_uuid=entity.uuid, preferred_value="BCV", fiat_currency="VES",
        fiat_amount=5000, valuation_at=at,
    )
    service.create_manual(
        client_uuid=entity.uuid, preferred_value="USDT", fiat_currency="VES",
        fiat_amount=2500, valuation_at=at,
    )

    totals = service.list_for_client(entity.uuid)["totals"]

    by_reference = {row["currency"]: row["amount"] for row in totals["by_reference"]}
    assert by_reference["USD_BCV"] == pytest.approx(12.5)
    assert by_reference["USDT"] == pytest.approx(5.0)
    # 12.5 USD BCV → 5000 VES → 10 USDT, más los 5 USDT del segundo préstamo.
    assert totals["usdt_total"] == pytest.approx(15.0)
    assert totals["warnings"] == []


def test_totals_survive_a_missing_rate(db, ves_rates, entity):
    service = ClientLoanService(db)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    service.create_manual(
        client_uuid=entity.uuid, preferred_value="FIAT", fiat_currency="VES",
        fiat_amount=5000, valuation_at=at,
    )
    # Se cae la tasa activa VES/USDT: el subtotal sigue, el total en USDT no se puede.
    db.query(ExchangeRate).update({ExchangeRate.is_active: False})
    db.flush()

    totals = service.list_for_client(entity.uuid)["totals"]

    assert totals["by_reference"] == [{"currency": "VES", "amount": pytest.approx(5000.0)}]
    assert totals["usdt_total"] is None
    assert len(totals["warnings"]) == 1


def test_paid_loans_do_not_count_towards_the_total(db, ves_rates, entity):
    service = ClientLoanService(db)
    at = datetime.now(timezone.utc) - timedelta(hours=2)
    loan = service.create_manual(
        client_uuid=entity.uuid, preferred_value="BCV", fiat_currency="VES",
        fiat_amount=5000, valuation_at=at,
    )
    service.add_repayment(UUID(loan["uuid"]), 12.5)

    totals = service.list_for_client(entity.uuid)["totals"]

    assert totals["by_reference"] == []
    assert totals["usdt_total"] == pytest.approx(0.0)
```

- [x] **Step 2: Correr los tests y verificar que fallan**

Run: `cd backend && pytest tests/test_client_loans.py -v -k totals`
Expected: FAIL con `KeyError: 'totals'`.

- [x] **Step 3: Implementar los totales**

En `backend/app/services/client_loan_service.py`, agregar antes de `list_for_client`:

```python
    def _outstanding_in_usdt(self, currency: str, amount: float) -> Optional[float]:
        """
        Cuánto valen hoy en USDT `amount` unidades de una referencia. Devuelve None si
        falta la tasa: preferimos no dar una cifra a dar una inventada.
        """
        if amount <= LOAN_EPSILON:
            return 0.0
        if currency == "USDT":
            return float(amount)
        try:
            if currency == "USD_BCV":
                # La deuda está en dólares a tasa oficial: se pasa por bolívares, que es
                # donde esa referencia tiene precio.
                rate = get_cached_bcv_rate(self.db)
                if rate is None or rate <= 0:
                    return None
                converted, _ = self._convert(float(amount) * float(rate), "VES", "USDT")
                return converted
            converted, _ = self._convert(float(amount), currency, "USDT")
            return converted
        except QuoteServiceError:
            return None

    def _totals(self, loans: list[ClientLoan]) -> dict:
        by_reference: dict[str, float] = {}
        for loan in loans:
            if loan.status not in (ClientLoanStatus.OPEN, ClientLoanStatus.PARTIAL):
                continue
            outstanding = loan.outstanding_amount
            if outstanding <= LOAN_EPSILON:
                continue
            key = loan.preferred_currency
            by_reference[key] = by_reference.get(key, 0.0) + outstanding

        warnings: list[str] = []
        usdt_total: Optional[float] = 0.0
        for currency, amount in by_reference.items():
            converted = self._outstanding_in_usdt(currency, amount)
            if converted is None:
                label = "USD (BCV)" if currency == "USD_BCV" else currency
                warnings.append(f"No hay tasa para convertir {label} a USDT")
                usdt_total = None
            elif usdt_total is not None:
                usdt_total += converted

        return {
            "by_reference": [
                {"currency": currency, "amount": round(amount, 8)}
                for currency, amount in sorted(by_reference.items())
            ],
            "usdt_total": round(usdt_total, 8) if usdt_total is not None else None,
            "warnings": warnings,
        }
```

Y cambiar el `return` de `list_for_client`:

```python
        return {
            "client_uuid": client.uuid,
            "loans": [self.serialize(loan) for loan in loans],
            "totals": self._totals(loans),
        }
```

- [x] **Step 4: Correr toda la suite de préstamos**

Run: `cd backend && pytest tests/test_client_loans.py tests/test_client_entities.py -v`
Expected: PASS (21 tests).

> Nota: en la ejecución real dieron 22, no 21 — `test_client_entities.py` ya traía un séptimo
> test (`test_long_name_fits_in_the_phone_column`) agregado en el commit `0224b80` ("fix: keep
> entity keys inside the phone column width"), ajeno a este plan. Los 3 tests nuevos de esta
> tarea pasan igual; no se tocó ese test preexistente.

- [x] **Step 5: Commit**

```bash
cd backend
git add app/services/client_loan_service.py tests/test_client_loans.py
git commit -m "feat: report a client's total outstanding debt"
```

---

### Task 6: Front — tipos, servicios y alta de la entidad

**Files:**
- Modify: `frontend/src/types/client.ts:53-99` (tipos de préstamo) y el tipo `ClientData`
- Modify: `frontend/src/utils/functions.ts:128-139`
- Create: `frontend/src/utils/clientPhone.test.ts`
- Modify: `frontend/src/services/clientService.ts`
- Modify: `frontend/src/services/paymentService.ts:162-184` (`createLoan`) y el tipo `LoanValuation`
- Create: `frontend/src/app/admin/clients/_components/NewEntityDialog.tsx`
- Modify: `frontend/src/app/admin/clients/page.tsx`, `frontend/src/app/admin/clients/_hooks/useClients.ts`

**Interfaces:**
- Consumes: `POST /clients` (Task 2), `GET|POST /clients/{uuid}/loans*` (Tasks 4-5), `requires_borrower`/`suggested_client` del preview (Task 3).
- Produces:
  - `isEntityClientPhone(phone: string | null | undefined): boolean`
  - `clientService.createEntity(data: { display_name: string; linked_group_jid?: string | null }): Promise<ApiResponse<ClientData>>`
  - `clientService.getManualLoanValuation(clientUuid, amount, currency, at): Promise<ApiResponse<ManualLoanValuation>>`
  - `clientService.createManualLoan(clientUuid, body): Promise<ApiResponse<LoanData>>`
  - Tipos `LoanTotals`, `ManualLoanValuation`; `ClientLoansSummary.totals`; `LoanData.outgoing_payment_id: number | null`

- [x] **Step 1: Escribir el test que falla**

Crear `frontend/src/utils/clientPhone.test.ts`:

```ts
import { describe, expect, it } from 'vitest';
import { isEntityClientPhone, isUnassignedClientPhone } from './functions';

describe('isEntityClientPhone', () => {
  it('reconoce a un cliente-entidad', () => {
    expect(isEntityClientPhone('entity:bodegon-x')).toBe(true);
  });

  it('no confunde a una entidad con un anónimo', () => {
    expect(isUnassignedClientPhone('entity:bodegon-x')).toBe(false);
    expect(isEntityClientPhone('anon:group:1')).toBe(false);
    expect(isEntityClientPhone('584121234567')).toBe(false);
    expect(isEntityClientPhone(null)).toBe(false);
  });
});
```

- [x] **Step 2: Correr el test y verificar que falla**

Run: `cd frontend && npm run test -- clientPhone`
Expected: FAIL — `isEntityClientPhone is not a function`.

- [x] **Step 3: Agregar el helper**

En `frontend/src/utils/functions.ts`, después de `isUnassignedClientPhone`:

```ts
/**
 * ¿Este cliente es un negocio sin teléfono propio en el bot? Espejo de
 * `client_entity_service.is_entity_client_phone`. A diferencia de un anónimo, una entidad
 * sí es un cliente de verdad: tiene ficha, historial y deuda.
 */
export const isEntityClientPhone = (phone: string | null | undefined): boolean =>
  !!phone && phone.startsWith('entity:');
```

- [x] **Step 4: Correr el test y verificar que pasa**

Run: `cd frontend && npm run test -- clientPhone`
Expected: PASS.

- [x] **Step 5: Actualizar los tipos**

En `frontend/src/types/client.ts`:

```ts
export interface LoanTotals {
  by_reference: { currency: string; amount: number }[];
  usdt_total: number | null;
  warnings: string[];
}

export interface ManualLoanValuation {
  fiat_amount: number;
  fiat_currency: string;
  usdt_amount: number | null;
  usdt_rate: number | null;
  bcv_amount: number | null;
  bcv_rate: number | null;
  valuation_at: string;
  warnings: string[];
}

export interface ManualLoanCreate {
  preferred_value: LoanPreferredValue;
  fiat_currency: string;
  fiat_amount: number;
  valuation_at: string;
  usdt_amount?: number | null;
  bcv_amount?: number | null;
  notes?: string | null;
}
```

En `LoanData`, cambiar `outgoing_payment_id: number;` por `outgoing_payment_id: number | null;`.
En `ClientLoansSummary`, agregar `totals: LoanTotals;`.
En `ClientData` (y en `ClientUpdate`), agregar `linked_group_jid: string | null;` / `linked_group_jid?: string | null;`.

En `frontend/src/services/paymentService.ts`, al tipo `LoanValuation` (donde esté declarado — buscar `interface LoanValuation`) agregar:

```ts
  requires_borrower: boolean;
  suggested_client: { uuid: string; display_name: string | null } | null;
```

Y en `createLoan`, agregar `clientUuid?: string | null;` al objeto `body` y `client_uuid: body.clientUuid ?? null,` al payload.

- [x] **Step 6: Agregar los métodos al servicio de clientes**

En `frontend/src/services/clientService.ts` (importar los tipos nuevos):

```ts
  // Alta de un negocio sin teléfono propio. Requiere moderador+ en el backend.
  async createEntity(data: {
    display_name: string;
    linked_group_jid?: string | null;
  }): Promise<ApiResponse<ClientData>> {
    const result = await httpClient.post<ClientData>('/clients', {
      display_name: data.display_name,
      linked_group_jid: data.linked_group_jid ?? null,
    });
    return { success: result.success, data: result.data, error: result.error };
  }

  async getManualLoanValuation(
    clientUuid: string,
    amount: number,
    currency: string,
    at: string,
  ): Promise<ApiResponse<ManualLoanValuation>> {
    const params = new URLSearchParams({ amount: String(amount), currency, at });
    const result = await httpClient.get<ManualLoanValuation>(
      `/clients/${clientUuid}/loans/valuation?${params.toString()}`,
    );
    return { success: result.success, data: result.data, error: result.error };
  }

  async createManualLoan(
    clientUuid: string,
    body: ManualLoanCreate,
  ): Promise<ApiResponse<LoanData>> {
    const result = await httpClient.post<LoanData>(`/clients/${clientUuid}/loans`, body);
    return { success: result.success, data: result.data, error: result.error };
  }
```

- [x] **Step 7: Diálogo de alta de entidad**

Crear `frontend/src/app/admin/clients/_components/NewEntityDialog.tsx`:

```tsx
'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

interface NewEntityDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (displayName: string, groupJid: string | null) => Promise<boolean>;
}

export function NewEntityDialog({ open, onOpenChange, onCreate }: NewEntityDialogProps) {
  const [name, setName] = useState('');
  const [groupJid, setGroupJid] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (!name.trim()) return toast.error('Ponle un nombre al negocio');
    setSubmitting(true);
    const ok = await onCreate(name.trim(), groupJid.trim() || null);
    setSubmitting(false);
    if (ok) {
      setName('');
      setGroupJid('');
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nuevo cliente-entidad</DialogTitle>
          <DialogDescription>
            Un negocio que no escribe al bot desde un teléfono propio. Sirve para llevarle
            préstamos y deuda.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="entity-name">Nombre del negocio</Label>
            <Input
              id="entity-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="Bodegón X"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="entity-group">Grupo de WhatsApp (opcional)</Label>
            <Input
              id="entity-group"
              value={groupJid}
              onChange={(event) => setGroupJid(event.target.value)}
              placeholder="1203630000000@g.us"
            />
            <p className="text-xs text-muted-foreground">
              Si mandas los comprobantes a un grupo, pégalo aquí y el préstamo lo propondrá
              solo cuando registres un pago de ese grupo.
            </p>
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancelar
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? 'Creando…' : 'Crear'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [x] **Step 8: Conectar el diálogo a la página**

En `frontend/src/app/admin/clients/_hooks/useClients.ts`, agregar dentro del hook (antes del `return`):

```ts
  const createEntity = useCallback(
    async (displayName: string, groupJid: string | null): Promise<boolean> => {
      const result = await clientService.createEntity({
        display_name: displayName,
        linked_group_jid: groupJid,
      });
      if (!result.success) {
        toast.error(result.error || 'No se pudo crear la entidad');
        return false;
      }
      toast.success('Entidad creada');
      loadClients();
      return true;
    },
    [loadClients],
  );
```

Agregar `import { toast } from 'sonner';` si no está, y `createEntity` al objeto `actions` que devuelve el hook.

En `frontend/src/app/admin/clients/page.tsx`:

```tsx
'use client';

import { useState } from 'react';
import { Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { PageHeader } from '@/components/shared/PageHeader';
import { ClientsStats } from './_components/ClientsStats';
import { ClientsFilters } from './_components/ClientsFilters';
import { ClientsList } from './_components/ClientsList';
import { NewEntityDialog } from './_components/NewEntityDialog';
import { useClients } from './_hooks/useClients';

export default function ClientsAdminPage() {
  const { state, actions } = useClients();
  const [entityOpen, setEntityOpen] = useState(false);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Clientes"
        description="Clientes del bot de WhatsApp: nombres, seguimiento y bloqueos."
        actions={
          <Button variant="outline" onClick={() => setEntityOpen(true)}>
            <Plus className="h-4 w-4" />
            Nuevo cliente-entidad
          </Button>
        }
      />

      <NewEntityDialog
        open={entityOpen}
        onOpenChange={setEntityOpen}
        onCreate={actions.createEntity}
      />

      <ClientsStats stats={state.stats} />

      <ClientsFilters
        filters={state.filters}
        hasActiveFilters={state.hasActiveFilters}
        onChange={actions.setFilters}
        onReset={actions.resetFilters}
      />

      <ClientsList
        clients={state.clients}
        loading={state.loading}
        error={state.error}
        hasActiveFilters={state.hasActiveFilters}
        hiddenCount={state.hiddenCount}
        onResetFilters={actions.resetFilters}
        onRetry={actions.reload}
      />
    </div>
  );
}
```

Si `PageHeader` no acepta `actions`, revisar `frontend/src/components/shared/PageHeader.tsx` y agregar la prop `actions?: React.ReactNode` renderizada a la derecha del título.

- [x] **Step 9: Mostrar la entidad como entidad en la lista**

En `frontend/src/app/admin/clients/_components/ClientItem.tsx`, donde se pinta `client.phone`, envolver con:

```tsx
{isEntityClientPhone(client.phone) ? (
  <span className="text-xs text-muted-foreground">
    Entidad{client.linked_group_jid ? ' · grupo vinculado' : ''}
  </span>
) : (
  /* el bloque del teléfono que ya existía */
)}
```

Importar `isEntityClientPhone` de `@/utils/functions`.

- [x] **Step 10: Verificar compilación y flujo**

Run: `cd frontend && npm run test && npm run build`
Expected: tests PASS, build sin errores de tipo.

Manual: en `/admin/clients`, crear "Bodegón X" con un JID; aparece en la lista mostrando "Entidad" en vez de un teléfono; su ficha abre sin errores.

- [x] **Step 11: Commit**

```bash
cd frontend
git add src/types/client.ts src/utils/functions.ts src/utils/clientPhone.test.ts src/services/clientService.ts src/services/paymentService.ts src/app/admin/clients
git commit -m "feat: create and display entity clients"
```

---

### Task 7: Front — total de la deuda y alta manual en la pestaña Préstamos

**Files:**
- Create: `frontend/src/components/loans/LoanReferenceFields.tsx`
- Create: `frontend/src/app/admin/clients/[uuid]/_components/NewLoanDialog.tsx`
- Modify: `frontend/src/app/admin/clients/[uuid]/_components/ClientLoansTab.tsx`
- Modify: `frontend/src/app/admin/clients/[uuid]/_hooks/useClientProfile.ts`
- Modify: `frontend/src/app/admin/clients/[uuid]/page.tsx` (pasar las props nuevas a la pestaña)

**Interfaces:**
- Consumes: `clientService.getManualLoanValuation`, `clientService.createManualLoan`, tipos `LoanTotals`/`ManualLoanValuation` (Task 6).
- Produces:
  - `LoanReferenceFields` con props `{ fiatCurrencyLabel: string; bcvEnabled: boolean; preferredValue: LoanPreferredValue; onPreferredValueChange: (value: LoanPreferredValue) => void; fiatAmount: string; usdtAmount: string; bcvAmount: string; onFiatAmountChange: (value: string) => void; onUsdtAmountChange: (value: string) => void; onBcvAmountChange: (value: string) => void; idPrefix: string }`
  - `useClientProfile()` expone `state.loanTotals: LoanTotals | null` y `actions.createLoan(body: ManualLoanCreate): Promise<boolean>`

- [ ] **Step 1: Crear el componente compartido de referencias**

Crear `frontend/src/components/loans/LoanReferenceFields.tsx`:

```tsx
'use client';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import type { LoanPreferredValue } from '@/types/client';

interface LoanReferenceFieldsProps {
  fiatCurrencyLabel: string;
  /** Solo el bolívar tiene tasa oficial: el backend rechaza BCV en cualquier otro par. */
  bcvEnabled: boolean;
  preferredValue: LoanPreferredValue;
  onPreferredValueChange: (value: LoanPreferredValue) => void;
  fiatAmount: string;
  usdtAmount: string;
  bcvAmount: string;
  onFiatAmountChange: (value: string) => void;
  onUsdtAmountChange: (value: string) => void;
  onBcvAmountChange: (value: string) => void;
  idPrefix: string;
}

/** Deja el importe con dos decimales como máximo, sin pelear con el cursor. */
const withTwoDecimals = (value: string) => {
  const clean = value.replace(',', '.').replace(/[^\d.]/g, '');
  const [whole, ...rest] = clean.split('.');
  if (rest.length === 0) return whole;
  return `${whole}.${rest.join('').slice(0, 2)}`;
};

/**
 * Elegir la referencia de la deuda y escribir los tres importes son lo mismo: cada unidad
 * es una tarjeta que se elige y donde se escribe. Compartido por el alta desde comprobante
 * y el alta manual para que las dos hablen igual.
 */
export function LoanReferenceFields({
  fiatCurrencyLabel,
  bcvEnabled,
  preferredValue,
  onPreferredValueChange,
  fiatAmount,
  usdtAmount,
  bcvAmount,
  onFiatAmountChange,
  onUsdtAmountChange,
  onBcvAmountChange,
  idPrefix,
}: LoanReferenceFieldsProps) {
  const references = [
    {
      value: 'FIAT' as const,
      label: fiatCurrencyLabel || 'Fiat',
      hint: 'valor fiat',
      id: `${idPrefix}-fiat-amount`,
      amount: fiatAmount,
      set: onFiatAmountChange,
      disabled: false,
    },
    {
      value: 'USDT' as const,
      label: 'USDT',
      hint: 'equivalente USDT',
      id: `${idPrefix}-usdt-amount`,
      amount: usdtAmount,
      set: onUsdtAmountChange,
      disabled: false,
    },
    {
      value: 'BCV' as const,
      label: 'USD BCV',
      hint: 'equivalente BCV',
      id: `${idPrefix}-bcv-amount`,
      amount: bcvAmount,
      set: onBcvAmountChange,
      disabled: !bcvEnabled,
    },
  ];

  return (
    <div className="space-y-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <Label>Referencia para llevar la deuda</Label>
        <span className="text-xs text-muted-foreground">
          la deuda se conserva en la unidad que elijas
        </span>
      </div>
      <div
        className="grid grid-cols-1 gap-2 sm:grid-cols-3"
        role="radiogroup"
        aria-label="Referencia para llevar la deuda"
      >
        {references.map((ref) => {
          const selected = preferredValue === ref.value;
          return (
            <div
              key={ref.value}
              className={cn(
                'rounded-lg border bg-card p-2.5 transition-colors',
                selected ? 'border-primary ring-3 ring-primary/10' : 'border-border',
                ref.disabled && 'opacity-60',
              )}
            >
              <button
                type="button"
                role="radio"
                aria-checked={selected}
                disabled={ref.disabled}
                onClick={() => onPreferredValueChange(ref.value)}
                className="flex min-h-10 w-full items-center justify-between gap-2 text-left"
              >
                <span
                  className={cn(
                    'truncate text-xs font-semibold',
                    selected ? 'text-primary' : 'text-foreground',
                  )}
                >
                  {ref.label}
                </span>
                <span
                  aria-hidden
                  className={cn(
                    'h-3.5 w-3.5 shrink-0 rounded-full border-2',
                    selected ? 'border-[4px] border-primary' : 'border-border',
                  )}
                />
              </button>
              <Input
                id={ref.id}
                inputMode="decimal"
                min="0"
                step="0.01"
                value={ref.amount}
                onChange={(event) => ref.set(withTwoDecimals(event.target.value))}
                placeholder={ref.disabled ? 'No aplica' : '0.00'}
                disabled={ref.disabled}
                aria-label={`Valor en ${ref.label}`}
                className="h-9 tabular-nums"
              />
              <p
                className={cn(
                  'mt-1 truncate text-[10.5px]',
                  selected ? 'font-semibold text-primary' : 'text-muted-foreground',
                )}
              >
                {selected ? 'referencia de la deuda' : ref.hint}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Diálogo de alta manual**

Crear `frontend/src/app/admin/clients/[uuid]/_components/NewLoanDialog.tsx`:

```tsx
'use client';

import { useCallback, useEffect, useState } from 'react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { LoanReferenceFields } from '@/components/loans/LoanReferenceFields';
import { clientService } from '@/services/clientService';
import type { LoanPreferredValue, ManualLoanCreate } from '@/types/client';

interface NewLoanDialogProps {
  clientUuid: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreate: (body: ManualLoanCreate) => Promise<boolean>;
}

const CURRENCIES = ['VES', 'COP', 'BRL', 'USD'];

const toNumber = (value: string): number | null => {
  const parsed = Number.parseFloat(value.replace(',', '.'));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
};

export function NewLoanDialog({ clientUuid, open, onOpenChange, onCreate }: NewLoanDialogProps) {
  const [currency, setCurrency] = useState('VES');
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 16));
  const [fiatAmount, setFiatAmount] = useState('');
  const [usdtAmount, setUsdtAmount] = useState('');
  const [bcvAmount, setBcvAmount] = useState('');
  const [preferredValue, setPreferredValue] = useState<LoanPreferredValue>('BCV');
  const [notes, setNotes] = useState('');
  const [warnings, setWarnings] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  // Las equivalencias se piden al backend con la fecha del préstamo: valorar con la tasa
  // de hoy un préstamo de la semana pasada falsea la deuda.
  const loadValuation = useCallback(async () => {
    const amount = toNumber(fiatAmount);
    if (!amount) return;
    const at = new Date(date).toISOString();
    const result = await clientService.getManualLoanValuation(clientUuid, amount, currency, at);
    if (!result.success || !result.data) {
      setWarnings([result.error || 'No se pudieron calcular las equivalencias']);
      return;
    }
    setWarnings(result.data.warnings);
    setUsdtAmount(result.data.usdt_amount != null ? result.data.usdt_amount.toFixed(2) : '');
    setBcvAmount(result.data.bcv_amount != null ? result.data.bcv_amount.toFixed(2) : '');
  }, [clientUuid, currency, date, fiatAmount]);

  useEffect(() => {
    if (currency !== 'VES' && preferredValue === 'BCV') setPreferredValue('USDT');
  }, [currency, preferredValue]);

  const submit = async () => {
    const amount = toNumber(fiatAmount);
    if (!amount) return toast.error('Indica el monto del préstamo');
    setSubmitting(true);
    const ok = await onCreate({
      preferred_value: preferredValue,
      fiat_currency: currency,
      fiat_amount: amount,
      valuation_at: new Date(date).toISOString(),
      usdt_amount: toNumber(usdtAmount),
      bcv_amount: currency === 'VES' ? toNumber(bcvAmount) : null,
      notes: notes.trim() || null,
    });
    setSubmitting(false);
    if (ok) {
      setFiatAmount('');
      setUsdtAmount('');
      setBcvAmount('');
      setNotes('');
      onOpenChange(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Registrar préstamo</DialogTitle>
          <DialogDescription>
            Para pagos que no pasaron por el bot. Las equivalencias se calculan con las
            tasas de la fecha que indiques.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="loan-currency">Moneda</Label>
              <select
                id="loan-currency"
                value={currency}
                onChange={(event) => setCurrency(event.target.value)}
                className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm"
              >
                {CURRENCIES.map((symbol) => (
                  <option key={symbol} value={symbol}>{symbol}</option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="loan-date">Fecha del préstamo</Label>
              <Input
                id="loan-date"
                type="datetime-local"
                value={date}
                onChange={(event) => setDate(event.target.value)}
                onBlur={loadValuation}
              />
            </div>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="loan-amount">Monto en {currency}</Label>
            <Input
              id="loan-amount"
              inputMode="decimal"
              value={fiatAmount}
              onChange={(event) => setFiatAmount(event.target.value)}
              onBlur={loadValuation}
              placeholder="0.00"
              className="tabular-nums"
            />
          </div>

          {warnings.map((warning) => (
            <p key={warning} className="text-xs text-amber-700 dark:text-amber-400">{warning}</p>
          ))}

          <LoanReferenceFields
            idPrefix="manual-loan"
            fiatCurrencyLabel={currency}
            bcvEnabled={currency === 'VES'}
            preferredValue={preferredValue}
            onPreferredValueChange={setPreferredValue}
            fiatAmount={fiatAmount}
            usdtAmount={usdtAmount}
            bcvAmount={bcvAmount}
            onFiatAmountChange={setFiatAmount}
            onUsdtAmountChange={setUsdtAmount}
            onBcvAmountChange={setBcvAmount}
          />

          <div className="space-y-1.5">
            <Label htmlFor="loan-notes">Nota (opcional)</Label>
            <Textarea
              id="loan-notes"
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Factura de luz de julio"
            />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)} disabled={submitting}>
            Cancelar
          </Button>
          <Button onClick={submit} disabled={submitting}>
            {submitting ? 'Registrando…' : 'Registrar préstamo'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
```

- [ ] **Step 3: Guardar los totales y el alta en el hook**

En `frontend/src/app/admin/clients/[uuid]/_hooks/useClientProfile.ts`:

```ts
  const [loanTotals, setLoanTotals] = useState<LoanTotals | null>(null);
```

En `loadLoans`, reemplazar el cuerpo por:

```ts
    setLoansLoading(true);
    const result = await clientService.getClientLoans(uuid);
    setLoans(result.success && result.data ? result.data.loans : []);
    setLoanTotals(result.success && result.data ? result.data.totals : null);
    setLoansLoading(false);
```

Y agregar la acción:

```ts
  const createLoan = useCallback(
    async (body: ManualLoanCreate): Promise<boolean> => {
      const result = await clientService.createManualLoan(uuid, body);
      if (!result.success) {
        toast.error(result.error || 'No se pudo registrar el préstamo');
        return false;
      }
      toast.success('Préstamo registrado');
      loadLoans();
      return true;
    },
    [uuid, loadLoans],
  );
```

Exponer `loanTotals` en `state` y `createLoan` en `actions`. Importar `LoanTotals` y `ManualLoanCreate` de `@/types/client`.

- [ ] **Step 4: Totales y botón en la pestaña**

En `ClientLoansTab.tsx`:

1. Props nuevas:

```tsx
interface ClientLoansTabProps {
  clientUuid: string;
  loans: LoanData[];
  totals: LoanTotals | null;
  loading: boolean;
  onRepayment: (loanUuid: string, amount: number, notes?: string | null) => Promise<boolean>;
  onCreateLoan: (body: ManualLoanCreate) => Promise<boolean>;
}
```

2. Borrar el `useMemo` de `totals` (líneas 52-58) y el `openLoans` si deja de usarse: los subtotales ahora vienen del backend.

3. El `EmptyState` deja de ser un `return` temprano: con cero préstamos igual hay que poder registrar uno. Sustituir el bloque `if (loans.length === 0) { return (<EmptyState .../>) }` por un render condicional dentro del árbol principal, y agregar el encabezado con el botón:

```tsx
  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end">
        <Button variant="outline" onClick={() => setCreating(true)}>
          <HandCoins className="h-4 w-4" />
          Registrar préstamo
        </Button>
      </div>

      <NewLoanDialog
        clientUuid={clientUuid}
        open={creating}
        onOpenChange={setCreating}
        onCreate={onCreateLoan}
      />

      {totals && totals.by_reference.length > 0 ? (
        <Card>
          <CardContent className="space-y-2 p-4 sm:p-5">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <HandCoins className="h-5 w-5 text-amber-600 dark:text-amber-400" />
              Deuda pendiente
            </div>
            {totals.usdt_total != null ? (
              <p className="text-2xl font-bold tabular-nums text-foreground">
                {formatAmount(totals.usdt_total, 'USDT')}
              </p>
            ) : null}
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {totals.by_reference.map((row) => (
                <span key={row.currency} className="text-sm font-semibold text-muted-foreground">
                  {formatAmount(row.amount, row.currency)}
                </span>
              ))}
            </div>
            {totals.warnings.map((warning) => (
              <p key={warning} className="text-xs text-amber-700 dark:text-amber-400">{warning}</p>
            ))}
          </CardContent>
        </Card>
      ) : null}

      {loans.length === 0 ? (
        <EmptyState
          icon={HandCoins}
          title="Sin préstamos"
          description="Registra uno a mano o marca un pago saliente como préstamo desde la bandeja."
        />
      ) : (
        <div className="space-y-3">
          {/* la lista de tarjetas que ya existe, sin cambios */}
        </div>
      )}
    </div>
  );
```

4. En la tarjeta de cada préstamo, el título asume comprobante. Cambiar:

```tsx
<h3 className="text-sm font-semibold text-foreground">Pago saliente #{loan.outgoing_payment_id}</h3>
```

por:

```tsx
<h3 className="text-sm font-semibold text-foreground">
  {loan.outgoing_payment_id != null ? `Pago saliente #${loan.outgoing_payment_id}` : 'Préstamo sin comprobante'}
</h3>
```

- [ ] **Step 5: Pasar las props desde la página**

En `frontend/src/app/admin/clients/[uuid]/page.tsx`, donde se renderiza `<ClientLoansTab ... />`, agregar `clientUuid={uuid}`, `totals={state.loanTotals}` y `onCreateLoan={actions.createLoan}`.

- [ ] **Step 6: Verificar**

Run: `cd frontend && npm run test && npm run build`
Expected: PASS y build limpio.

Manual, con el backend corriendo: en la ficha de la entidad → pestaña Préstamos → "Registrar préstamo" con 5000 VES, fecha de ayer y referencia USD BCV. Se crea, la tarjeta dice "Préstamo sin comprobante" y arriba aparece el total en USDT con el subtotal en USD (BCV).

- [ ] **Step 7: Commit**

```bash
cd frontend
git add src/components/loans src/app/admin/clients/\[uuid\] src/services/clientService.ts
git commit -m "feat: show total debt and register loans by hand"
```

---

### Task 8: Front — elegir el deudor en el diálogo del comprobante

**Files:**
- Modify: `frontend/src/app/admin/payments/_components/OutgoingPaymentActionDialog.tsx` (paso `loan`: selector de deudor y uso de `LoanReferenceFields`)

**Interfaces:**
- Consumes: `LoanReferenceFields` (Task 7), `clientService.getClients` (ya existe), `LoanValuation.requires_borrower` / `.suggested_client` (Task 6), `paymentService.createLoan({..., clientUuid})` (Task 6).
- Produces: nada que consuman otras tareas.

- [ ] **Step 1: Estado del deudor**

Junto a los demás `useState` del diálogo (~línea 99):

```tsx
  const [borrowerUuid, setBorrowerUuid] = useState<string | null>(null);
  const [borrowerOptions, setBorrowerOptions] = useState<{ uuid: string; label: string }[]>([]);
```

- [ ] **Step 2: Cargar candidatos y preseleccionar la entidad**

Dentro del `useEffect`/callback que ya carga la valuación (`loadLoanValuation`), después de guardar el resultado:

```tsx
      if (data.suggested_client) setBorrowerUuid(data.suggested_client.uuid);
```

Y un efecto nuevo que sólo corre cuando hace falta elegir:

```tsx
  // Cuando el comprobante se mandó a un grupo, el teléfono no dice a quién se le prestó:
  // hay que elegir el deudor. Se ofrecen los clientes-entidad primero.
  useEffect(() => {
    if (step !== 'loan' || !valuation?.requires_borrower) return;
    let cancelled = false;
    void clientService.getClients({ limit: 500 }).then((result) => {
      if (cancelled || !result.success || !result.data) return;
      const items = result.data.items
        .filter((item) => !isUnassignedClientPhone(item.phone))
        .map((item) => ({
          uuid: item.uuid,
          label: item.display_name || item.phone,
        }));
      setBorrowerOptions(items);
    });
    return () => {
      cancelled = true;
    };
  }, [step, valuation?.requires_borrower]);
```

Importar `clientService` de `@/services/clientService` e `isUnassignedClientPhone` de `@/utils/functions`.

- [ ] **Step 3: Pintar el selector**

En el paso `loan`, justo antes del bloque de referencias:

```tsx
                {valuation?.requires_borrower ? (
                  <div className="space-y-1.5">
                    <Label htmlFor="loan-borrower">¿A nombre de quién queda el préstamo?</Label>
                    <select
                      id="loan-borrower"
                      value={borrowerUuid ?? ''}
                      onChange={(event) => setBorrowerUuid(event.target.value || null)}
                      className="h-10 w-full rounded-md border border-border bg-background px-3 text-sm"
                    >
                      <option value="">Selecciona el deudor</option>
                      {borrowerOptions.map((option) => (
                        <option key={option.uuid} value={option.uuid}>{option.label}</option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">
                      El comprobante se mandó a un grupo, así que el teléfono no identifica al
                      deudor.
                    </p>
                  </div>
                ) : null}
```

- [ ] **Step 4: Reemplazar el bloque inline por el componente compartido**

Sustituir todo el bloque de las tarjetas de referencia (desde `<div className="space-y-1.5">` con el `Label` "Referencia para llevar la deuda", ~línea 668, hasta el `</div>` que cierra el `role="radiogroup"`, ~línea 771) por:

```tsx
                <LoanReferenceFields
                  idPrefix="loan"
                  fiatCurrencyLabel={fiatCurrency}
                  bcvEnabled={fiatCurrency.trim().toUpperCase() === 'VES'}
                  preferredValue={preferredValue}
                  onPreferredValueChange={setPreferredValue}
                  fiatAmount={fiatAmount}
                  usdtAmount={usdtAmount}
                  bcvAmount={bcvAmount}
                  onFiatAmountChange={setFiatAmount}
                  onUsdtAmountChange={setUsdtAmount}
                  onBcvAmountChange={setBcvAmount}
                />
```

Importar `LoanReferenceFields` de `@/components/loans/LoanReferenceFields`. Borrar `setAmountWithTwoDecimals` si queda sin usar (la lógica vive ahora en el componente).

- [ ] **Step 5: Mandar el deudor y bloquear el envío sin él**

En el handler que llama a `paymentService.createLoan` (~línea 355), agregar la guarda y el campo:

```tsx
    if (valuation?.requires_borrower && !borrowerUuid) {
      toast.error('Elige a nombre de quién queda el préstamo');
      return;
    }
```

y dentro del objeto que se manda:

```tsx
      clientUuid: borrowerUuid,
```

- [ ] **Step 6: Verificar**

Run: `cd frontend && npm run build`
Expected: build limpio.

Manual, con backend corriendo: en `/admin/payments` (salientes), abrir un comprobante mandado al grupo del negocio → "Préstamo". Aparece el selector con la entidad preseleccionada; al confirmar, el préstamo queda en la ficha de la entidad. Repetir con un comprobante de un teléfono normal: el selector no aparece y el flujo es el de siempre.

- [ ] **Step 7: Commit**

```bash
cd frontend
git add src/app/admin/payments/_components/OutgoingPaymentActionDialog.tsx
git commit -m "feat: pick the borrower when a loan receipt came from a group"
```

---

## Verificación final

- [ ] `cd backend && pytest -v` — toda la suite en verde (no solo los archivos nuevos).
- [ ] `cd frontend && npm run test && npm run build`.
- [ ] Recorrido completo en local: crear la entidad con su grupo → mandar un comprobante al grupo desde el bot (o crear el pago saliente a mano) → marcarlo como préstamo con el deudor preseleccionado → registrar un segundo préstamo a mano en USD BCV → ver el total en USDT con los dos subtotales → abonar hasta cerrar uno y comprobar que sale del total.
- [ ] `cd backend && alembic downgrade -1 && alembic upgrade head` sobre una copia de la BD local, para confirmar que la migración va y viene.
