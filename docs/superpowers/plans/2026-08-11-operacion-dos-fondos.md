# La operación mueve los dos fondos que toca — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Que una operación registre en el libro las dos patas que mueve —el fondo que recibe sube, el que paga baja— en vez del único movimiento con signo invertido que hay hoy.

**Architecture:** Una columna nueva `fund_group_out_id` en `whatsapp_operations` guarda el fondo de la pata que sale; `fund_group_id` conserva su significado (la pata que entra). Un tipo de movimiento nuevo `EXCHANGE_IN` suma al balance donde `EXCHANGE` resta. Un único helper `_sync_fund_legs(op, actor)` deja el libro igual a lo que dice la operación. Los fondos se resuelven por la moneda **al crear la operación** y se persisten; el sync solo los lee, para que el override manual no se pise solo.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres, pytest contra Postgres real.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-11-operacion-dos-fondos-design.md`. Lo que el spec dice manda; este plan lo ejecuta.
- **Rama:** `feat/operacion-dos-fondos` (ya creada, sobre `origin/main`).
- **Commits en inglés** (`backend/CLAUDE.md`).
- **Los tests corren contra Postgres real** en `localhost:5433`; si no está, `conftest.py` los saltea. Comando: `../venv/bin/python -m pytest` desde `backend/`.
- **Nada de movimientos retroactivos para el histórico sin fondo.** Fuera de alcance por decisión del usuario.
- **`amount` en `fund_movements` es siempre positivo**; el tipo determina el signo. No introducir importes negativos.
- **`FundRepository.create_movement` hace `commit()`.** Dentro del helper de sync NO se usa: se crean las filas con `self.db.add()` + `flush()`, para no commitear a mitad de una operación de servicio.

---

## File Structure

| Archivo | Responsabilidad | Acción |
|---|---|---|
| `app/models/fund.py` | Enum `FundMovementType` gana `EXCHANGE_IN` | Modificar |
| `app/repositories/fund_repository.py` | Los tres cálculos que cuentan movimientos + resolver fondo por moneda | Modificar |
| `app/models/whatsapp_operation.py` | Columna `fund_group_out_id`, relación y `dict()` | Modificar |
| `alembic/versions/a1b2c3d4e5f6_add_operation_out_fund.py` | Migración aditiva de la columna | Crear |
| `app/services/whatsapp_payment_service.py` | `_sync_fund_legs`, resolución al crear, borrar `_fund_movement_figures`/`_sync_fund_movement` | Modificar |
| `app/services/whatsapp_quote_service.py` | Resolver fondos al cotizar; `_apply_scenario` acepta el fondo saliente | Modificar |
| `app/schemas/whatsapp.py` | `fund_group_out_uuid` / `clear_fund_group_out` | Modificar |
| `app/services/profit_allocation_service.py` | El default de ganancia lee el fondo que pagó | Modificar |
| `app/routers/transaction.py` | La transacción manual crea `EXCHANGE_IN` | Modificar |
| `app/cli/backfill_fund_legs.py` | Arrastre de los 31 movimientos, con `--dry-run` | Crear |
| `tests/test_fund_legs.py` | Todo lo de las patas: signos, resolución, sync, override | Crear |
| `tests/test_fund_legs_backfill.py` | Los tres tratos del arrastre | Crear |

---

### Task 1: `EXCHANGE_IN` cuenta como entrada

**Files:**
- Modify: `app/models/fund.py:11-15`
- Modify: `app/repositories/fund_repository.py:290-297` (saldo corriente), `:474-508` (`get_user_position`), `:530-551` (`get_group_balance`)
- Test: `tests/test_fund_legs.py` (crear)

**Interfaces:**
- Produces: `FundMovementType.EXCHANGE_IN` — el valor de enum que usan todas las tareas siguientes para la pata que entra.

No lleva migración: `fund_movements.movement_type` es `varchar(20)` con `CaseInsensitiveEnum`, así que el valor nuevo entra sin tocar el esquema.

- [ ] **Step 1: Escribir el test que falla**

```python
"""Las dos patas de una operación en el libro de fondos."""

from datetime import datetime, timezone

import pytest

from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.repositories.fund_repository import FundRepository


def _mov(db, group, user, mtype, amount, currency, usdt):
    row = FundMovement(
        group_id=group.id, user_id=user.id, movement_type=mtype,
        amount=amount, currency=currency, amount_usdt=usdt,
        movement_date=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def test_exchange_in_suma_donde_exchange_resta(db, fund, operator):
    """La pata que entra sube el fondo; la que sale lo baja."""
    entra = _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    sale = _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    saldos = FundRepository(db).get_running_totals(fund.id, [entra.id, sale.id])

    assert saldos[entra.id]["balance_usdt"] == 100
    assert saldos[sale.id]["balance_usdt"] == 60


def test_exchange_in_cuenta_como_entrada_en_la_posicion(db, fund, operator):
    """`position` se lee 'el fondo le debe al gestor': la pata que entra la aumenta."""
    _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    pos = FundRepository(db).get_user_position(operator.id, fund.id)

    assert pos["total_deposited_usdt"] == 100
    assert pos["total_outflow_usdt"] == 40
    assert pos["position_usdt"] == 60


def test_exchange_in_cuenta_como_entrada_en_el_balance_del_grupo(db, fund, operator):
    _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    balance = FundRepository(db).get_group_balance(fund.id)

    assert balance["total_position_usdt"] == 60
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: FAIL con `AttributeError: EXCHANGE_IN`

- [ ] **Step 3: Agregar el valor al enum**

En `app/models/fund.py`:

```python
class FundMovementType(enum.Enum):
    DEPOSIT     = "DEPOSIT"      # Gestor deposita USD al fondo (Binance/Kraken → Zelle)
    EXCHANGE    = "EXCHANGE"     # La pata que SALE del fondo: se le pagó al cliente
    EXCHANGE_IN = "EXCHANGE_IN"  # La pata que ENTRA al fondo: el cliente nos pagó
    PERSONAL    = "PERSONAL"     # Gasto personal del gestor con fondos del fondo (queda como deuda)
    ADJUSTMENT  = "ADJUSTMENT"   # Corrección manual
```

Y en el docstring del modelo `FundMovement` (`app/models/fund.py:150-156`) agregar la línea:

```
    - EXCHANGE_IN: pata que entra → aumenta posición, igual que un depósito
```

- [ ] **Step 4: Sumarlo en los tres cálculos**

En `app/repositories/fund_repository.py`, saldo corriente (~línea 290):

```python
        signed_amount = sa_case(
            (FundMovement.movement_type.in_(
                [FundMovementType.DEPOSIT, FundMovementType.EXCHANGE_IN]),
             reversal_signed(FundMovement.amount_usdt)),
            (FundMovement.movement_type.in_(
                [FundMovementType.EXCHANGE, FundMovementType.PERSONAL]),
             -reversal_signed(FundMovement.amount_usdt)),
            else_=0,
        )
```

En `get_user_position` (~línea 490), el filtro de depósitos:

```python
        ).filter(
            FundMovement.group_id == group_id,
            FundMovement.user_id == user_id,
            FundMovement.movement_type.in_(
                [FundMovementType.DEPOSIT, FundMovementType.EXCHANGE_IN]
            )
        ).first()
```

En `get_group_balance` (~línea 535), el mismo cambio en su `deposit_result`.

- [ ] **Step 5: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Correr la suite entera para ver que nada se movió**

Run: `../venv/bin/python -m pytest -q`
Expected: 357 passed (o más)

- [ ] **Step 7: Commit**

```bash
git add app/models/fund.py app/repositories/fund_repository.py tests/test_fund_legs.py
git commit -m "feat: EXCHANGE_IN movements count as money entering the fund"
```

---

### Task 2: La columna del fondo saliente

**Files:**
- Modify: `app/models/whatsapp_operation.py:92` (columna), `:158` (relación), `:177-215` (`dict()`)
- Create: `alembic/versions/a1b2c3d4e5f6_add_operation_out_fund.py`
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Produces: `WhatsAppOperation.fund_group_out_id` (int, nullable), `WhatsAppOperation.fund_group_out` (relación), y en `op.dict()` las claves `fund_group_out_uuid` y `fund_group_out_name`.

- [ ] **Step 1: Escribir el test que falla**

Agregar a `tests/test_fund_legs.py`:

```python
def test_la_operacion_guarda_el_fondo_que_paga(db, fund, pairs, client, operator):
    """La pata que sale tiene su propio fondo, y sale en el dict de la op."""
    from datetime import timedelta

    from app.models.whatsapp_operation import (
        WhatsAppOperation, WhatsAppOperationStatus,
    )

    brasil = FundGroup(name="Cambios Brasil test", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-BRL"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side="SEND",
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency="ZELLE",
        fund_group_id=fund.id, fund_group_out_id=brasil.id,
        created_at=now, quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()

    assert op.fund_group_out.name == "Cambios Brasil test"
    assert op.dict()["fund_group_out_uuid"] == brasil.uuid
    assert op.dict()["fund_group_out_name"] == "Cambios Brasil test"
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py::test_la_operacion_guarda_el_fondo_que_paga -v`
Expected: FAIL con `TypeError: 'fund_group_out_id' is an invalid keyword argument`

- [ ] **Step 3: Agregar columna, relación y claves del dict**

En `app/models/whatsapp_operation.py`, junto a `fund_group_id` (línea 92):

```python
    # El fondo de la pata que ENTRA (lo que el cliente entrega) es `fund_group_id`; este es
    # el de la pata que SALE (lo que le pagamos). Una operación mueve la caja de los dos.
    fund_group_out_id = Column(
        Integer, ForeignKey("fund_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )
```

Junto a la relación (línea 158):

```python
    fund_group_out = relationship("FundGroup", foreign_keys=[fund_group_out_id])
```

En `dict()`, después de `fund_group_name`:

```python
            "fund_group_out_uuid": self.fund_group_out.uuid if self.fund_group_out else None,
            "fund_group_out_name": self.fund_group_out.name if self.fund_group_out else None,
```

- [ ] **Step 4: Escribir la migración**

Confirmar la cabeza actual antes de escribir:

```bash
../venv/bin/python -m alembic heads
```

Debe decir `18e341e018c3 (head)`. Si dice otra cosa, usar esa como `down_revision`.

`alembic/versions/a1b2c3d4e5f6_add_operation_out_fund.py`:

```python
"""operation records the fund that paid

Revision ID: a1b2c3d4e5f6
Revises: 18e341e018c3
Create Date: 2026-08-11
"""

from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "18e341e018c3"
branch_labels = None
depends_on = None


def upgrade():
    # Aditiva: `fund_group_id` sigue siendo la pata que entra y no se toca ninguna fila.
    op.add_column(
        "whatsapp_operations",
        sa.Column("fund_group_out_id", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_whatsapp_operations_fund_group_out_id",
        "whatsapp_operations",
        ["fund_group_out_id"],
    )
    op.create_foreign_key(
        "fk_whatsapp_operations_fund_group_out_id",
        "whatsapp_operations",
        "fund_groups",
        ["fund_group_out_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint(
        "fk_whatsapp_operations_fund_group_out_id", "whatsapp_operations", type_="foreignkey"
    )
    op.drop_index("ix_whatsapp_operations_fund_group_out_id", table_name="whatsapp_operations")
    op.drop_column("whatsapp_operations", "fund_group_out_id")
```

- [ ] **Step 5: Correr el test y la migración**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (4 tests)

Run: `../venv/bin/python -m alembic upgrade head && ../venv/bin/python -m alembic downgrade -1 && ../venv/bin/python -m alembic upgrade head`
Expected: las tres corren sin error (el ida y vuelta prueba el `downgrade`)

- [ ] **Step 6: Commit**

```bash
git add app/models/whatsapp_operation.py alembic/versions/a1b2c3d4e5f6_add_operation_out_fund.py tests/test_fund_legs.py
git commit -m "feat: operations record the fund that paid the outgoing leg"
```

---

### Task 3: Resolver el fondo de una moneda

**Files:**
- Modify: `app/repositories/fund_repository.py` (método nuevo, al lado de los demás getters de grupo)
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Produces: `FundRepository.get_active_group_by_currency(currency: str) -> Optional[FundGroup]` — devuelve `None` si hay cero o más de un fondo activo con esa moneda. Las tareas 4, 5 y 6 la usan.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_el_fondo_de_una_moneda_se_resuelve_solo(db, fund):
    """ZELLE liquida como USD, así que cae en el fondo USD."""
    from app.services.valuation import settlement_currency

    repo = FundRepository(db)

    assert repo.get_active_group_by_currency(settlement_currency("ZELLE")).id == fund.id
    assert repo.get_active_group_by_currency("USD").id == fund.id


def test_una_moneda_sin_fondo_no_resuelve(db, fund):
    """Los bolívares no tienen fondo: esa pata no deja movimiento."""
    assert FundRepository(db).get_active_group_by_currency("VES") is None


def test_dos_fondos_de_la_misma_moneda_no_resuelven(db, fund):
    """Ante dos candidatos el sistema no adivina: lo elige el operador."""
    otro = FundGroup(name="Otro USD", currency="USD", is_active=True)
    db.add(otro)
    db.flush()

    assert FundRepository(db).get_active_group_by_currency("USD") is None


def test_un_fondo_inactivo_no_compite(db, fund):
    """Un fondo desactivado no cuenta ni para resolver ni para ambiguar."""
    viejo = FundGroup(name="USD viejo", currency="USD", is_active=False)
    db.add(viejo)
    db.flush()

    assert FundRepository(db).get_active_group_by_currency("USD").id == fund.id
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k moneda -v`
Expected: FAIL con `AttributeError: 'FundRepository' object has no attribute 'get_active_group_by_currency'`

- [ ] **Step 3: Implementar el método**

En `app/repositories/fund_repository.py`:

```python
    def get_active_group_by_currency(self, currency: Optional[str]) -> Optional[FundGroup]:
        """
        El fondo activo que lleva esa moneda, o None.

        Devuelve None también cuando hay MÁS de uno: entre dos fondos de la misma moneda no
        hay forma de elegir sin inventar, así que la pata queda sin fondo y lo resuelve el
        operador a mano. Hoy los tres fondos tienen monedas distintas, pero el día que haya
        dos en USD esto tiene que callarse, no adivinar.
        """
        if not currency:
            return None
        rows = (
            self.db.query(FundGroup)
            .filter(FundGroup.is_active.is_(True), func.upper(FundGroup.currency) == currency.upper())
            .limit(2)
            .all()
        )
        return rows[0] if len(rows) == 1 else None
```

- [ ] **Step 4: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Commit**

```bash
git add app/repositories/fund_repository.py tests/test_fund_legs.py
git commit -m "feat: resolve the fund that holds a currency, silent when ambiguous"
```

---

### Task 4: `_sync_fund_legs` — el libro sigue a la operación

**Files:**
- Modify: `app/services/whatsapp_payment_service.py` — agregar `_sync_fund_legs`, borrar `_fund_movement_figures` (`:1379-1409`) y `_sync_fund_movement` (`:1411-1432`), cambiar el llamador (`:1371`)
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Consumes: `FundMovementType.EXCHANGE_IN` (Task 1), `WhatsAppOperation.fund_group_out_id` (Task 2).
- Produces: `WhatsAppPaymentService._sync_fund_legs(op: WhatsAppOperation, actor: Optional[User] = None) -> None`. Idempotente: correrla dos veces deja el mismo libro. Las tareas 5 y 6 la llaman.

Reglas que implementa:

1. Solo una operación `COMPLETED` tiene movimientos. En cualquier otro estado, los borra (una op cancelada no deja plata movida).
2. Pata que entra: `from_amount` en `settlement_currency(from_currency)`, tipo `EXCHANGE_IN`, fondo `op.fund_group_id`.
3. Pata que sale: `to_amount` en `settlement_currency(to_currency)`, tipo `EXCHANGE`, fondo `op.fund_group_out_id`.
4. Pata sin fondo → sin movimiento (y si había uno, se borra).
5. El `amount_usdt` de cada pata sale de `valuation.equivalents` a `op.valuation_at or op.quoted_at`.
6. Gestor: `actor`, si no `op.received_by_user_id`. Sin ninguno de los dos, la pata no se registra — pero nunca revienta la operación.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def _op_completada(db, pairs, client, fund_in, fund_out, operator):
    from datetime import timedelta

    from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-BRL"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side="SEND",
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency="ZELLE",
        amount_usdt=100, usdt_rate=1,
        fund_group_id=fund_in.id if fund_in else None,
        fund_group_out_id=fund_out.id if fund_out else None,
        received_by_user_id=operator.id,
        created_at=now, quoted_at=now, valuation_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    return op


def test_una_operacion_de_dos_fondos_deja_las_dos_patas(db, pairs, client, fund, operator):
    """El caso 3251: entran 100 USD al fondo Zelle, salen 465,75 BRL del de Brasil."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, brasil, operator)

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    movs = db.query(FundMovement).all()
    entra = [m for m in movs if m.movement_type == FundMovementType.EXCHANGE_IN]
    sale = [m for m in movs if m.movement_type == FundMovementType.EXCHANGE]

    assert len(entra) == 1 and entra[0].group_id == fund.id
    assert entra[0].amount == 100 and entra[0].currency == "USD"
    assert len(sale) == 1 and sale[0].group_id == brasil.id
    assert sale[0].amount == 465.75 and sale[0].currency == "BRL"


def test_la_pata_sin_fondo_no_deja_movimiento(db, pairs, client, fund, operator):
    """El caso normal: pagamos en bolívares, que no tienen fondo."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    movs = db.query(FundMovement).all()
    assert len(movs) == 1 and movs[0].movement_type == FundMovementType.EXCHANGE_IN


def test_correrlo_dos_veces_no_duplica(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppPaymentService(db)

    svc._sync_fund_legs(op, operator)
    svc._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 1


def test_quitarle_el_fondo_borra_su_pata(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppPaymentService(db)
    svc._sync_fund_legs(op, operator)

    op.fund_group_id = None
    db.flush()
    svc._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 0


def test_una_operacion_no_completada_no_mueve_el_fondo(db, pairs, client, fund, operator):
    """Una cotización no movió plata todavía."""
    from app.models.whatsapp_operation import WhatsAppOperationStatus
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    op.status = WhatsAppOperationStatus.QUOTED
    db.flush()

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 0


def test_cambiar_el_valor_reajusta_las_dos_patas(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, brasil, operator)
    svc = WhatsAppPaymentService(db)
    svc._sync_fund_legs(op, operator)

    op.from_amount = 50
    op.to_amount = 232.88
    db.flush()
    svc._sync_fund_legs(op, operator)

    montos = {m.movement_type: m.amount for m in db.query(FundMovement).all()}
    assert montos[FundMovementType.EXCHANGE_IN] == 50
    assert montos[FundMovementType.EXCHANGE] == 232.88
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k patas -v`
Expected: FAIL con `AttributeError: '_sync_fund_legs'`

- [ ] **Step 3: Implementar el helper**

En `app/services/whatsapp_payment_service.py`, en el lugar donde hoy está `_sync_fund_movement`:

```python
    def _fund_leg_specs(self, op: WhatsAppOperation) -> list[tuple]:
        """
        Las patas que la operación declara: (fondo_id, tipo, monto, moneda).

        La pata que entra es lo que el cliente da; la que sale, lo que le pagamos. Cada una va
        en SU moneda, que por construcción es la del fondo que la lleva — por eso acá no hay
        ninguna conversión de moneda (la había `_fund_movement_figures`, que existía para
        expresar el valor de la op en la moneda de un fondo distinto).
        """
        cp = op.currency_pair
        if cp is None:
            return []
        from_symbol = cp.from_currency.symbol if cp.from_currency else None
        to_symbol = cp.to_currency.symbol if cp.to_currency else None

        specs = []
        if op.fund_group_id and op.from_amount and from_symbol:
            specs.append((
                op.fund_group_id,
                FundMovementType.EXCHANGE_IN,
                round(float(op.from_amount), 2),
                valuation.settlement_currency(from_symbol),
            ))
        if op.fund_group_out_id and op.to_amount and to_symbol:
            specs.append((
                op.fund_group_out_id,
                FundMovementType.EXCHANGE,
                round(float(op.to_amount), 2),
                valuation.settlement_currency(to_symbol),
            ))
        return specs

    def _sync_fund_legs(self, op: WhatsAppOperation, actor: Optional[User] = None) -> None:
        """
        Deja el libro igual a lo que dice la operación: hasta dos movimientos, uno por pata
        con fondo. Crea, actualiza y borra; correrla dos veces deja el mismo resultado.

        Reemplaza a `_sync_fund_movement` (que solo reajustaba) y a la creación suelta dentro
        de `create_operation_from_payment`: eran dos caminos que podían divergir.

        No usa `FundRepository.create_movement` a propósito: ese hace `commit()`, y esto corre
        dentro de operaciones de servicio que todavía no terminaron.
        """
        existing = {
            m.movement_type: m
            for m in self.db.query(FundMovement)
            .filter(FundMovement.transaction_id == op.transaction_id)
            .all()
        } if op.transaction_id is not None else {}

        # Solo una operación completada movió plata. Cancelarla o devolverla a cotizada
        # retira sus movimientos.
        specs = (
            self._fund_leg_specs(op)
            if op.status == WhatsAppOperationStatus.COMPLETED
            else []
        )

        user_id = (actor.id if actor else None) or op.received_by_user_id
        if specs and user_id is None:
            # Sin gestor no se puede registrar, pero eso nunca tumba la operación.
            specs = []

        at = op.valuation_at or op.quoted_at or datetime.now(timezone.utc)
        wanted_types = set()
        for group_id, mtype, amount, currency in specs:
            wanted_types.add(mtype)
            eq = valuation.equivalents(self.db, amount, currency, at)
            usdt = eq["usdt_amount"]
            rate = (amount / usdt) if usdt else None

            movement = existing.get(mtype)
            if movement is None:
                movement = FundMovement(
                    movement_type=mtype,
                    transaction_id=op.transaction_id,
                    movement_date=at,
                    user_id=user_id,
                    recorded_by_user_id=actor.id if actor else None,
                )
                self.db.add(movement)
            movement.group_id = group_id
            movement.amount = amount
            movement.currency = currency
            movement.amount_usdt = usdt
            movement.usdt_rate = rate

        for mtype, movement in existing.items():
            if mtype in (FundMovementType.EXCHANGE, FundMovementType.EXCHANGE_IN) and mtype not in wanted_types:
                self.db.delete(movement)

        self.db.flush()
```

Verificar que los imports que usa ya estén en el módulo (`FundMovement`, `FundMovementType`, `valuation`, `WhatsAppOperationStatus`, `User`, `Optional`, `datetime`); agregar los que falten.

- [ ] **Step 4: Borrar lo que queda huérfano y redirigir al llamador**

Borrar `_fund_movement_figures` y `_sync_fund_movement` completos. En `update_value` (~línea 1371), cambiar:

```python
        WhatsAppQuoteService(self.db)._sync_linked_transaction(op)
        self._sync_fund_legs(op, actor)
```

Confirmar que no quedan referencias:

```bash
grep -rn "_sync_fund_movement\|_fund_movement_figures" app/ tests/
```

Expected: sin resultados.

- [ ] **Step 5: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (14 tests)

Run: `../venv/bin/python -m pytest -q`
Expected: todo verde. Si algún test viejo esperaba un `EXCHANGE` donde ahora hay `EXCHANGE_IN`, actualizar el test — el cambio de tipo es intencional.

- [ ] **Step 6: Commit**

```bash
git add app/services/whatsapp_payment_service.py tests/test_fund_legs.py
git commit -m "feat: one helper keeps both fund legs in sync with the operation"
```

---

### Task 5: Resolver los fondos al crear la operación, y usar el helper

**Files:**
- Modify: `app/services/whatsapp_payment_service.py:1755-1900` (`create_operation_from_payment`), y `set_operation` / `update_status` donde completan una op
- Modify: `app/services/whatsapp_quote_service.py` (`create_quote`)
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Consumes: `get_active_group_by_currency` (Task 3), `_sync_fund_legs` (Task 4).
- Produces: `WhatsAppPaymentService._resolve_fund_legs_for_new_op(op) -> None`, que **persiste** los dos fondos en las columnas de una operación recién creada.

**Por qué la resolución corre al crear y no en cada sync:** si `_sync_fund_legs` re-resolviera, el "sin fondo" que el operador elija a mano se volvería a llenar solo en la siguiente corrida. Resolviendo una vez al nacer, las columnas son la verdad y el override se queda quieto. Consecuencia buscada: las operaciones **nuevas** mueven fondos solas; las viejas con columnas en NULL no generan nada hasta que alguien les asigne fondo a mano.

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_una_operacion_nueva_resuelve_sus_fondos_sola(db, fund, pairs, client, operator):
    """Nace con los fondos puestos según la moneda de cada pata."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()

    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=100, to_amount=465.75,
        user_uuid=operator.uuid, recorded_by=operator.id,
    )

    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()

    assert op.fund_group_id == fund.id
    assert op.fund_group_out_id == brasil.id


def test_el_fondo_elegido_a_mano_le_gana_a_la_resolucion(db, fund, pairs, client, operator):
    """Si el caller dice el fondo de entrada, no se pisa."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    otro = FundGroup(name="Otro", currency="USD", is_active=True)
    db.add(otro)
    db.flush()

    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=100, to_amount=465.75,
        fund_uuid=otro.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )

    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()

    assert op.fund_group_id == otro.id
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k resuelve -v`
Expected: FAIL — `op.fund_group_out_id is None`

- [ ] **Step 3: Implementar la resolución**

En `app/services/whatsapp_payment_service.py`:

```python
    def _resolve_fund_legs_for_new_op(self, op: WhatsAppOperation) -> None:
        """
        Completa los fondos que la operación no trae, por la moneda de cada pata.

        Corre UNA vez, al nacer la operación. Lo que el caller ya puso (el fondo elegido a
        mano, o el heredado del comprobante) no se pisa: solo se rellena lo que está en NULL.
        """
        cp = op.currency_pair
        if cp is None:
            return
        repo = FundRepository(self.db)
        if op.fund_group_id is None and cp.from_currency:
            group = repo.get_active_group_by_currency(
                valuation.settlement_currency(cp.from_currency.symbol)
            )
            op.fund_group_id = group.id if group else None
        if op.fund_group_out_id is None and cp.to_currency:
            group = repo.get_active_group_by_currency(
                valuation.settlement_currency(cp.to_currency.symbol)
            )
            op.fund_group_out_id = group.id if group else None
```

En `create_operation_from_payment`, justo después del `self.db.flush()` que sigue al `self.db.add(op)` (~línea 1848):

```python
        self._resolve_fund_legs_for_new_op(op)
        self.db.flush()
```

- [ ] **Step 4: Reemplazar la creación suelta del EXCHANGE por el helper**

El bloque `if group is not None:` (~líneas 1861-1897) se reemplaza **entero** por este. Cambia la condición (ahora basta con que cualquiera de las dos patas tenga fondo), desaparece el `create_movement` suelto y desaparece el `raise exchange_user_required`: sin gestor, `_sync_fund_legs` simplemente no registra las patas (regla 6 de la Task 4) en vez de tumbar la creación de la operación.

```python
        # Con fondo en cualquiera de las dos patas hace falta la transacción, para que los
        # movimientos cuelguen de ella y se vayan con ella (FK ON DELETE CASCADE) si la op
        # se borra.
        if op.fund_group_id or op.fund_group_out_id:
            exchange_user_id = recorded_by_user_id
            if exchange_user_uuid is not None:
                user = self.db.query(User).filter(User.uuid == exchange_user_uuid).first()
                if user is None:
                    raise QuoteServiceError(
                        "user_not_found", f"Usuario {exchange_user_uuid} no encontrado", 404
                    )
                exchange_user_id = user.id

            transaction_user = (
                self.db.query(User).filter(User.id == exchange_user_id).first()
                if exchange_user_id
                else None
            )
            if transaction_user is None:
                raise QuoteServiceError(
                    "transaction_user_required", "Falta el usuario de la transacción", 400
                )

            tx = quote_svc._create_transaction_for_op(
                op, WhatsAppOperationComplete(), transaction_user
            )
            op.transaction_id = tx.id
            self._sync_fund_legs(op, transaction_user)
```

- [ ] **Step 5: Llamar al helper donde una operación se completa**

Hoy, completar una operación por otro camino que no sea crearla desde un comprobante no deja movimiento. Con las patas eso se cierra. Ubicar los tres sitios:

```bash
grep -n "def set_operation\|def update_status\|def create_quote" app/services/whatsapp_payment_service.py app/services/whatsapp_quote_service.py
```

**En `set_operation`** (payment service, vincular un saliente que completa la op), justo antes del `self.db.commit()` final:

```python
        self._sync_fund_legs(op, completing_user)
```

Si la variable del operador se llama distinto en ese scope, usar la que exista; lo importante es que sea el `User` que ejecuta la acción, no `None`.

**En `update_status`** (quote service), dentro de la rama que ya crea la transacción, después de `op.completed_at = now`:

```python
        from app.services.whatsapp_payment_service import WhatsAppPaymentService

        WhatsAppPaymentService(self.db)._sync_fund_legs(op, operator)
```

El import va adentro de la función a propósito: `whatsapp_payment_service` ya importa `whatsapp_quote_service`, y a nivel de módulo sería circular.

**En `create_quote`** (quote service), después del `self.db.add(op)` y su `flush()`, para que las operaciones que nacen de una cotización —las del bot, que son la mayoría— también traigan sus fondos:

```python
        from app.services.whatsapp_payment_service import WhatsAppPaymentService

        WhatsAppPaymentService(self.db)._resolve_fund_legs_for_new_op(op)
        self.db.flush()
```

- [ ] **Step 6: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (16 tests)

Run: `../venv/bin/python -m pytest -q`
Expected: todo verde. Prestar atención a `tests/test_operation_linking_rules.py` y `tests/test_fund_capital_from_receipt.py`, que ejercitan estos caminos.

- [ ] **Step 7: Commit**

```bash
git add app/services/whatsapp_payment_service.py app/services/whatsapp_quote_service.py tests/test_fund_legs.py
git commit -m "feat: new operations resolve and record both of their funds"
```

---

### Task 6: Editar los fondos a mano

**Files:**
- Modify: `app/schemas/whatsapp.py:169-183` (`WhatsAppOperationScenarioUpdate`)
- Modify: `app/services/whatsapp_quote_service.py:840-860` (`_apply_scenario`)
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Consumes: `_sync_fund_legs` (Task 4).
- Produces: los campos `fund_group_out_uuid` y `clear_fund_group_out` en el body de `PATCH /operations/{uuid}/scenario`.

- [ ] **Step 1: Escribir el test que falla**

```python
def test_asignar_el_fondo_saliente_a_mano_crea_su_pata(db, fund, pairs, client, operator):
    from app.schemas.whatsapp import WhatsAppOperationScenarioUpdate
    from app.services.whatsapp_quote_service import WhatsAppQuoteService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, None, operator)

    WhatsAppQuoteService(db).set_scenario(
        op.uuid,
        WhatsAppOperationScenarioUpdate(fund_group_out_uuid=brasil.uuid),
        operator,
    )

    sale = db.query(FundMovement).filter(
        FundMovement.movement_type == FundMovementType.EXCHANGE
    ).all()
    assert len(sale) == 1 and sale[0].group_id == brasil.id


def test_quitar_el_fondo_a_mano_borra_su_pata(db, fund, pairs, client, operator):
    from app.schemas.whatsapp import WhatsAppOperationScenarioUpdate
    from app.services.whatsapp_quote_service import WhatsAppQuoteService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppQuoteService(db)
    svc.set_scenario(op.uuid, WhatsAppOperationScenarioUpdate(), operator)

    svc.set_scenario(
        op.uuid, WhatsAppOperationScenarioUpdate(clear_fund_group=True), operator
    )

    assert db.query(FundMovement).count() == 0
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k mano -v`
Expected: FAIL con error de validación por `fund_group_out_uuid` desconocido

- [ ] **Step 3: Agregar los campos al schema**

En `app/schemas/whatsapp.py`:

La clase completa queda así (las dos líneas nuevas son `fund_group_out_uuid` y
`clear_fund_group_out`):

```python
class WhatsAppOperationScenarioUpdate(BaseModel):
    """
    Setear/editar el escenario, los fondos y el receptor del entrante de una operación.
    Todos opcionales (PATCH parcial). El fondo de la pata que ENTRA se resuelve por
    `fund_group_uuid` o, para el bot, por `group_jid` (FundGroup.whatsapp_group_jid);
    el de la pata que SALE, por `fund_group_out_uuid`.
    """
    scenario: Optional[Literal["NORMAL", "ZELLE_DIRECT", "VIA_PARTNER"]] = None
    fund_group_uuid: Optional[UUID] = None
    fund_group_out_uuid: Optional[UUID] = None
    group_jid: Optional[str] = None
    received_by_user_uuid: Optional[UUID] = None
    # Permite explícitamente limpiar los fondos / receptor (poner a NULL) cuando True.
    clear_fund_group: bool = False
    clear_fund_group_out: bool = False
    clear_received_by: bool = False
    # Reasigna la op a un cliente anónimo dedicado (VIA_PARTNER: el socio no es el cliente).
    anonymize_client: bool = False
```

- [ ] **Step 4: Aplicarlos en `_apply_scenario`**

Después del bloque que ya resuelve `fund_group_uuid` / `group_jid`, agregar el hermano:

```python
        if payload.clear_fund_group_out:
            op.fund_group_out_id = None
        elif payload.fund_group_out_uuid is not None:
            group_out = (
                self.db.query(FundGroup)
                .filter(FundGroup.uuid == str(payload.fund_group_out_uuid))
                .first()
            )
            if group_out is None:
                raise QuoteServiceError("fund_group_not_found", "FundGroup no encontrado", 404)
            op.fund_group_out_id = group_out.id
```

Y al final de `set_scenario`, después de `_apply_scenario` y del manejo de la transacción, sincronizar el libro:

```python
        from app.services.whatsapp_payment_service import WhatsAppPaymentService

        WhatsAppPaymentService(self.db)._sync_fund_legs(op, operator)
```

El `if op.fund_group_id is not None and op.transaction_id is None:` que crea la transacción tiene que contemplar también `op.fund_group_out_id`, o una op que solo tenga fondo saliente se quedaría sin transacción de la que colgar el movimiento:

```python
        if (op.fund_group_id or op.fund_group_out_id) and op.transaction_id is None:
```

- [ ] **Step 5: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (18 tests)

- [ ] **Step 6: Commit**

```bash
git add app/schemas/whatsapp.py app/services/whatsapp_quote_service.py tests/test_fund_legs.py
git commit -m "feat: the outgoing leg's fund can be set and cleared by hand"
```

---

### Task 7: La ganancia va al fondo que pagó

**Files:**
- Modify: `app/services/profit_allocation_service.py:58-94` (`ensure_defaults`)
- Test: `tests/test_fund_legs.py`

**Interfaces:**
- Consumes: `WhatsAppOperation.fund_group_out_id` (Task 2).

- [ ] **Step 1: Escribir los tests que fallan**

```python
def test_la_ganancia_por_defecto_va_al_fondo_que_pago(db, fund, pairs, client, operator):
    from app.services.profit_allocation_service import ProfitAllocationService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, brasil, operator)
    op.applied_percentage = 8
    db.flush()

    allocations = ProfitAllocationService(db).ensure_defaults(op)

    assert len(allocations) == 1
    assert allocations[0].fund_group_id == brasil.id


def test_sin_fondo_saliente_la_ganancia_queda_donde_estaba(db, fund, pairs, client, operator):
    """Las ops que pagan en bolívares se comportan exactamente como antes."""
    from app.services.profit_allocation_service import ProfitAllocationService

    op = _op_completada(db, pairs, client, fund, None, operator)
    op.applied_percentage = 8
    db.flush()

    allocations = ProfitAllocationService(db).ensure_defaults(op)

    assert len(allocations) == 1
    assert allocations[0].fund_group_id == fund.id
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k ganancia -v`
Expected: FAIL — la asignación va a `fund.id` en los dos casos

- [ ] **Step 3: Implementar**

En `ensure_defaults`, el cuerpo entre `existing` y la creación de la asignación queda así. Solo
cambian dos cosas: de dónde sale `group_id`, y que la consulta del grupo lo use.

```python
        existing = self.allocations(op)
        if existing:
            return existing

        # Gana el fondo que puso la plata. Si esa pata no tiene fondo (las operaciones que
        # pagan en bolívares, que son la mayoría), sigue ganando el de entrada, que es como
        # se repartía antes de que la operación tuviera dos patas.
        group_id = op.fund_group_out_id or op.fund_group_id
        if group_id is None:
            return []

        charged = float(op.applied_percentage or 0)
        if charged <= 0:
            return []

        group = self.db.query(FundGroup).filter(FundGroup.id == group_id).first()
        if group is None:
            return []
```

De ahí en adelante (el `default`, el `min`, el `OperationProfitAllocation(...)`) no se toca
nada. En el docstring, cambiar "todo al fondo que la atendió" por "todo al fondo que **pagó**;
si esa pata no tiene fondo, al que recibió".

- [ ] **Step 4: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -v`
Expected: PASS (20 tests)

Run: `../venv/bin/python -m pytest tests/test_profit_allocations.py -q`
Expected: verde

- [ ] **Step 5: Commit**

```bash
git add app/services/profit_allocation_service.py tests/test_fund_legs.py
git commit -m "feat: profit defaults to the fund that put the money out"
```

---

### Task 8: La transacción manual también registra una entrada

**Files:**
- Modify: `app/routers/transaction.py:220-231`
- Test: `tests/test_fund_legs.py`

`POST /transactions` crea su propio movimiento con `from_amount`/`from_currency` — o sea, la pata que **entra**. Pasa a `EXCHANGE_IN` por el mismo criterio que todo lo demás. No usa `_sync_fund_legs` porque no tiene una `WhatsAppOperation` detrás.

- [ ] **Step 1: Escribir el test que falla**

Va por HTTP con el patrón de `tests/test_client_accounts_endpoint.py`: `TestClient` sobre `app` con `dependency_overrides` para la sesión y el usuario, sin JWT real.

```python
def test_la_transaccion_manual_registra_una_entrada(db, fund, pairs, operator):
    """Lo que el cliente entrega ENTRA al fondo: el endpoint manual también lo registra así."""
    from starlette.testclient import TestClient

    from app.core.dependencies import get_current_user
    from app.database.connection import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: operator
    try:
        with TestClient(app, raise_server_exceptions=False) as api:
            resp = api.post("/transactions/", json={
                "currency_pair_uuid": str(pairs["ZELLE-BRL"].uuid),
                "from_amount": 100,
                "to_amount": 465.75,
                "total_profit_percentage": 0.0,
                "fund_group_uuid": str(fund.uuid),
                "profit_splits": [],
                "status": "completed",
            })
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code in (200, 201), resp.text
    movs = db.query(FundMovement).all()
    assert len(movs) == 1
    assert movs[0].movement_type == FundMovementType.EXCHANGE_IN
```

Si la ruta no es `/transactions/` o el body pide algún campo más, ajustarlo con
`grep -n "@router.post" app/routers/transaction.py` y el schema `TransactionCreate`
(`app/schemas/transaction.py:70`). El assert que importa es el del tipo de movimiento.

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `../venv/bin/python -m pytest tests/test_fund_legs.py -k transaccion_manual -v`
Expected: FAIL

- [ ] **Step 3: Cambiar el tipo**

En `app/routers/transaction.py:223`:

```python
                movement_type=FundMovementType.EXCHANGE_IN,
```

Y actualizar el comentario de arriba si menciona que sale plata del fondo: acá entra.

- [ ] **Step 4: Correr los tests**

Run: `../venv/bin/python -m pytest -q`
Expected: verde

- [ ] **Step 5: Commit**

```bash
git add app/routers/transaction.py tests/test_fund_legs.py
git commit -m "feat: a manual transaction records money entering the fund"
```

---

### Task 9: El arrastre de los 31 movimientos

**Files:**
- Create: `app/cli/backfill_fund_legs.py`
- Test: `tests/test_fund_legs_backfill.py`

**Interfaces:**
- Consumes: `FundMovementType.EXCHANGE_IN` (Task 1), `fund_group_out_id` (Task 2), `get_active_group_by_currency` (Task 3).
- Produces: `python -m app.cli.backfill_fund_legs [--dry-run|--apply]`. Sin flags, `--dry-run`.

Tres tratos, y el CLI los imprime por separado:

1. **Cambio de tipo** — el movimiento cuya moneda coincide con `settlement_currency(from_currency)` de su op pasa a `EXCHANGE_IN`. Mismo fondo, mismo monto (30 filas en prod).
2. **Se rehace** — el movimiento cuya moneda **no** coincide con ninguna de las dos patas está expresado en la moneda de un fondo distinto al de su pata (el `6172`: 106,85 BRL que son 21 USD). Se le recalcula fondo, monto y moneda desde la pata entrante (1 fila).
3. **Patas salientes que faltan** — para cada op con movimiento cuya moneda pagada resuelve a un fondo, se completa `fund_group_out_id` y se crea su `EXCHANGE` (4 filas: 3 BRL, 1 COP; incluye la op 3251).

- [ ] **Step 1: Escribir el test que falla**

`tests/test_fund_legs_backfill.py`:

```python
"""El arrastre de los movimientos viejos a las dos patas."""

from datetime import datetime, timedelta, timezone

from app.cli.backfill_fund_legs import plan_backfill
from app.models.fund import FundGroup, FundMovement, FundMovementType
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus


def _op_con_movimiento(db, pairs, client, group, amount, currency, frm, to, usdt):
    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs[f"{frm}-{to}"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side="SEND",
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency=frm,
        fund_group_id=group.id, created_at=now, quoted_at=now, valuation_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    mov = FundMovement(
        group_id=group.id, user_id=1, movement_type=FundMovementType.EXCHANGE,
        amount=amount, currency=currency, amount_usdt=usdt, movement_date=now,
    )
    db.add(mov)
    db.flush()
    return op, mov


def test_el_movimiento_de_la_pata_entrante_solo_cambia_de_tipo(db, fund, pairs, client):
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "BRL", 100)

    plan = plan_backfill(db)

    assert plan["retipar"] == [(mov.id, FundMovementType.EXCHANGE_IN)]
    assert plan["rehacer"] == []


def test_el_movimiento_en_la_moneda_del_otro_fondo_se_rehace(db, fund, pairs, client):
    """El caso 6172: 106,85 BRL que en realidad son los 21 USD que entregó el cliente."""
    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op, mov = _op_con_movimiento(db, pairs, client, brasil, 106.85, "BRL", "ZELLE", "BRL", 21)

    plan = plan_backfill(db)

    assert plan["retipar"] == []
    rehacer = plan["rehacer"][0]
    assert rehacer["movement_id"] == mov.id
    assert rehacer["group_id"] == fund.id      # pasa al fondo USD
    assert rehacer["currency"] == "USD"


def test_se_planifica_la_pata_saliente_que_falta(db, fund, pairs, client):
    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "BRL", 100)

    plan = plan_backfill(db)

    nueva = plan["crear_saliente"][0]
    assert nueva["operation_id"] == op.id
    assert nueva["group_id"] == brasil.id
    assert nueva["amount"] == 465.75


def test_una_op_que_paga_en_bolivares_no_genera_pata_saliente(db, fund, pairs, client):
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "VES", 100)

    plan = plan_backfill(db)

    assert plan["crear_saliente"] == []
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `../venv/bin/python -m pytest tests/test_fund_legs_backfill.py -v`
Expected: FAIL con `ModuleNotFoundError: app.cli.backfill_fund_legs`

- [ ] **Step 3: Escribir el CLI**

`app/cli/backfill_fund_legs.py`:

```python
"""
Lleva los movimientos viejos al modelo de dos patas.

Hasta ahora una operación dejaba UN movimiento `EXCHANGE`, siempre en el fondo de la moneda
que el cliente entrega y siempre restando del balance. Con el criterio de caja, esa pata
entra (`EXCHANGE_IN`) y falta la que sale.

Tres tratos, que el plan imprime por separado porque no son el mismo cambio:

  1. retipar        — la moneda del movimiento es la de la pata entrante: solo cambia el tipo.
  2. rehacer        — la moneda no es la de ninguna pata: el movimiento está expresado en la
                      moneda de un fondo distinto al que le toca. Se recalcula entero.
  3. crear_saliente — la moneda que pagamos resuelve a un fondo: falta su `EXCHANGE`.

    python -m app.cli.backfill_fund_legs              # dry-run: imprime y no escribe
    python -m app.cli.backfill_fund_legs --apply      # escribe
"""

import argparse

from sqlalchemy.orm import Session

from app.database.connection import SessionLocal
from app.models.fund import FundMovement, FundMovementType
from app.models.whatsapp_operation import WhatsAppOperation
from app.repositories.fund_repository import FundRepository
from app.services import valuation


def plan_backfill(db: Session) -> dict:
    """Qué habría que hacer con cada movimiento. No escribe nada."""
    repo = FundRepository(db)
    plan = {"retipar": [], "rehacer": [], "crear_saliente": []}

    movimientos = (
        db.query(FundMovement)
        .filter(
            FundMovement.transaction_id.isnot(None),
            FundMovement.movement_type == FundMovementType.EXCHANGE,
        )
        .order_by(FundMovement.id)
        .all()
    )

    for mov in movimientos:
        op = (
            db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.transaction_id == mov.transaction_id)
            .first()
        )
        if op is None or op.currency_pair is None:
            continue
        cp = op.currency_pair
        entra = valuation.settlement_currency(cp.from_currency.symbol) if cp.from_currency else None
        sale = valuation.settlement_currency(cp.to_currency.symbol) if cp.to_currency else None

        if mov.currency == entra:
            plan["retipar"].append((mov.id, FundMovementType.EXCHANGE_IN))
        else:
            # Expresado en la moneda de otro fondo: se recalcula desde la pata entrante.
            grupo_entra = repo.get_active_group_by_currency(entra)
            plan["rehacer"].append({
                "movement_id": mov.id,
                "group_id": grupo_entra.id if grupo_entra else None,
                "amount": round(float(op.from_amount or 0), 2),
                "currency": entra,
            })

        grupo_sale = repo.get_active_group_by_currency(sale)
        if grupo_sale is not None:
            plan["crear_saliente"].append({
                "operation_id": op.id,
                "transaction_id": op.transaction_id,
                "group_id": grupo_sale.id,
                "amount": round(float(op.to_amount or 0), 2),
                "currency": sale,
                "user_id": mov.user_id,
            })

    return plan


def apply_backfill(db: Session, plan: dict) -> None:
    """Escribe lo que dice el plan."""
    for movement_id, nuevo_tipo in plan["retipar"]:
        db.query(FundMovement).filter(FundMovement.id == movement_id).update(
            {"movement_type": nuevo_tipo}
        )

    for item in plan["rehacer"]:
        mov = db.query(FundMovement).filter(FundMovement.id == item["movement_id"]).first()
        if mov is None or item["group_id"] is None:
            continue
        eq = valuation.equivalents(db, item["amount"], item["currency"], mov.movement_date)
        mov.movement_type = FundMovementType.EXCHANGE_IN
        mov.group_id = item["group_id"]
        mov.amount = item["amount"]
        mov.currency = item["currency"]
        mov.amount_usdt = eq["usdt_amount"]
        mov.usdt_rate = (item["amount"] / eq["usdt_amount"]) if eq["usdt_amount"] else None

    for item in plan["crear_saliente"]:
        op = db.query(WhatsAppOperation).filter(
            WhatsAppOperation.id == item["operation_id"]
        ).first()
        if op is None:
            continue
        op.fund_group_out_id = item["group_id"]
        at = op.valuation_at or op.quoted_at
        eq = valuation.equivalents(db, item["amount"], item["currency"], at)
        db.add(FundMovement(
            group_id=item["group_id"],
            user_id=item["user_id"],
            movement_type=FundMovementType.EXCHANGE,
            amount=item["amount"],
            currency=item["currency"],
            amount_usdt=eq["usdt_amount"],
            usdt_rate=(item["amount"] / eq["usdt_amount"]) if eq["usdt_amount"] else None,
            transaction_id=item["transaction_id"],
            movement_date=at,
        ))

    db.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Arrastre de movimientos a las dos patas")
    parser.add_argument("--apply", action="store_true", help="escribe (por defecto: dry-run)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        plan = plan_backfill(db)
        print(f"\nretipar a EXCHANGE_IN: {len(plan['retipar'])}")
        for movement_id, _ in plan["retipar"]:
            print(f"  mov {movement_id}")
        print(f"\nrehacer (fondo y monto): {len(plan['rehacer'])}")
        for item in plan["rehacer"]:
            print(f"  mov {item['movement_id']} → fondo {item['group_id']} "
                  f"{item['amount']} {item['currency']}")
        print(f"\ncrear pata saliente: {len(plan['crear_saliente'])}")
        for item in plan["crear_saliente"]:
            print(f"  op {item['operation_id']} → fondo {item['group_id']} "
                  f"-{item['amount']} {item['currency']}")

        if args.apply:
            apply_backfill(db, plan)
            print("\nEscrito.")
        else:
            print("\nDry-run: no se escribió nada. Con --apply se aplica.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Correr los tests**

Run: `../venv/bin/python -m pytest tests/test_fund_legs_backfill.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Correr la suite entera**

Run: `../venv/bin/python -m pytest -q`
Expected: todo verde

- [ ] **Step 6: Commit**

```bash
git add app/cli/backfill_fund_legs.py tests/test_fund_legs_backfill.py
git commit -m "feat: CLI to migrate the old single-leg movements"
```

---

## Puesta en producción

No es una tarea de código, pero el plan no está terminado sin esto. En orden:

1. **Mirar una hoja real del Excel** y confirmar la lectura de `position` que el spec deja anotada: la pata que entra aumenta lo que el fondo le debe al gestor, igual que un depósito. Si el Excel dice otra cosa, es un cambio en los tres cálculos de la Task 1, no en el resto.
2. **Deploy del backend** (push a `main` → Action). Antes: `ssh tasas-ec2 'df -h /'`, que necesita ~2,5G libres. El bot pierde comprobantes durante los ~2,5 min de la ventana: avisar o pararlo.
3. **Dry-run del arrastre en prod**: `ssh tasas-ec2 'cd ~/cambios-los-criollitos-be && docker compose exec -T backend python -m app.cli.backfill_fund_legs'`. Tienen que salir 30 retipar, 1 rehacer y 4 crear.
4. **Aplicarlo** con `--apply` y verificar la op 3251: `EXCHANGE_IN +100 USD` en Zelle/Paypal y `EXCHANGE −465,75 BRL` en Cambios Brasil.
5. **Front**: el drawer de `/admin/operaciones` necesita el segundo `<select>`, y la lista de movimientos tiene que saber pintar `EXCHANGE_IN`. Va en su propio plan; hasta entonces los fondos salientes se asignan por API.
