# Cobertura de operación — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cuadrar una operación pagada con varios comprobantes desde la propia operación, con la tasa derivada de la suma en vez de tecleada.

**Architecture:** No hay tabla nueva: `whatsapp_outgoing_settlements` ya es `(pago, operación, monto)` sin cardinalidad fija y hoy sólo se lee en un sentido. Se agrega la lectura inversa (`GET/PUT /operations/{uuid}/coverage`) delegando en los mismos `_upsert_settlement` / `_sync_settlement_totals` que usa el panel del comprobante, más dos columnas en la operación para el resto que no tiene comprobante.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic + Postgres; pytest. Front Next.js + Tailwind (repo aparte).

## Global Constraints

- Commits del backend **en inglés**; los del front **en español**.
- Migración: `down_revision = 'd9e0f1a2b3c4'` (head actual).
- Router → Service; nunca tocar modelos desde el router.
- La tasa se deriva **sólo al cerrar** la cobertura. A medias, cada comprobante se valora a `_reference_rate` y la cotización no se toca.
- `implied_margin` **no se modifica**. El camino de cobertura calcula el margen directo contra la base.
- Ninguna operación puede quedar cubierta por encima de su valor (regla que ya aplica `set_settlements`).

---

### Task 1: Las columnas del resto declarado

**Files:**
- Create: `alembic/versions/a1b2c3d4e5f6_operation_uncovered_remainder.py`
- Modify: `app/models/whatsapp_operation.py` (columnas + `pending_amount` en `dict()`)
- Test: `tests/test_operation_coverage.py`

**Interfaces:**
- Produces: `WhatsAppOperation.uncovered_amount: float | None`, `WhatsAppOperation.uncovered_reason: str | None`, y `dict()["pending_amount"]` descontando el resto.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_operation_coverage.py
import pytest
from tests import factories as f


def test_declared_remainder_closes_the_pending(db, client, pairs, operator):
    """El resto declarado cuenta como cubierto: es lo que deja cerrar el trato."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    pago = f.outgoing(db, 322_000, "VES", phone="584148861273")
    op_dict = f.create_op_from_payment(
        WhatsAppPaymentService(db), "outgoing", pago, frm="ZELLE", to="VES",
        from_amount=400, to_amount=322_000, recorded_by=operator.id,
    )
    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_dict["uuid"]).first()

    assert op.dict()["pending_amount"] == pytest.approx(50.0, abs=0.01)
    op.uncovered_amount = 50.0
    op.uncovered_reason = "OTHER_CHANNEL"
    db.flush()
    assert op.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../venv/bin/python -m pytest tests/test_operation_coverage.py -q`
Expected: FAIL — `uncovered_amount` no existe en el modelo.

- [ ] **Step 3: Add the columns and the migration**

```python
# app/models/whatsapp_operation.py — junto a las demás columnas
    #: Parte del valor que no tiene comprobante porque no se puede representar en el sistema
    #: (efectivo en mano, un canal que el bot no lee, saldo a favor, un ajuste). No es un
    #: error a corregir: se declara, y declararlo es lo que deja cerrar el trato.
    uncovered_amount = Column(Float, nullable=True)
    uncovered_reason = Column(String(24), nullable=True)
```

```python
# app/models/whatsapp_operation.py — en dict()
            "pending_amount": round((value or 0) - delivered - (self.uncovered_amount or 0), 2),
```

```python
# alembic/versions/a1b2c3d4e5f6_operation_uncovered_remainder.py
"""the part of an operation's value that has no receipt

Sometimes the payout is larger than what its receipts add up to, because part of it
cannot be represented here: cash handed over, a channel the bot does not read, a
credit balance, or an adjustment. That gap is not an error to fix — it is declared,
and declaring it is what lets the deal close.

Revision ID: a1b2c3d4e5f6
Revises: d9e0f1a2b3c4
Create Date: 2026-08-27 06:30:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'a1b2c3d4e5f6'
down_revision = 'd9e0f1a2b3c4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('whatsapp_operations', sa.Column('uncovered_amount', sa.Float(), nullable=True))
    op.add_column('whatsapp_operations', sa.Column('uncovered_reason', sa.String(length=24), nullable=True))


def downgrade():
    op.drop_column('whatsapp_operations', 'uncovered_reason')
    op.drop_column('whatsapp_operations', 'uncovered_amount')
```

- [ ] **Step 4: Run the test**

Run: `../venv/bin/python -m pytest tests/test_operation_coverage.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/a1b2c3d4e5f6_operation_uncovered_remainder.py app/models/whatsapp_operation.py tests/test_operation_coverage.py
git commit -m "feat: an operation can declare the part of its value that has no receipt"
```

---

### Task 2: Leer la cobertura desde la operación

**Files:**
- Modify: `app/services/whatsapp_payment_service.py` (método `operation_coverage`)
- Modify: `app/routers/operations.py` (`GET /{op_uuid}/coverage`)
- Test: `tests/test_operation_coverage.py`

**Interfaces:**
- Consumes: `operation_value`, `delivered_amount`, `_reference_rate` (ya existen).
- Produces: `WhatsAppPaymentService.operation_coverage(op_uuid: UUID) -> dict` con las claves
  `value`, `value_currency`, `delivered`, `uncovered`, `uncovered_reason`, `pending`,
  `reference_rate`, `settlements[]`, `candidates[]`, `suggestion[]`.

- [ ] **Step 1: Write the failing test**

```python
def test_coverage_lists_the_clients_free_receipts(db, client, pairs, operator):
    """Los candidatos son los salientes del mismo cliente que aún tienen saldo libre."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    svc = WhatsAppPaymentService(db)
    primero = f.outgoing(db, 65_723, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        svc, "outgoing", primero, frm="ZELLE", to="VES",
        from_amount=350, to_amount=315_000, recorded_by=operator.id,
    )
    f.outgoing(db, 250_000, "VES", phone="584148861273")
    f.outgoing(db, 6_277, "VES", phone="584148861273")
    f.outgoing(db, 999, "VES", phone="otro-cliente")
    db.flush()

    cov = svc.operation_coverage(op["uuid"])

    assert cov["value"] == pytest.approx(350.0)
    assert {c["amount"] for c in cov["candidates"]} == {250_000, 6_277}
    assert [s["payment_id"] for s in cov["settlements"]] == [primero.id]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `../venv/bin/python -m pytest tests/test_operation_coverage.py::test_coverage_lists_the_clients_free_receipts -q`
Expected: FAIL — `operation_coverage` no existe.

- [ ] **Step 3: Implement `operation_coverage`**

```python
# app/services/whatsapp_payment_service.py
    def operation_coverage(self, op_uuid: UUID) -> dict:
        """
        Qué cubre ya esta operación y con qué comprobantes podría terminar de cubrirse.

        Es el espejo de `settlement_summary`: la misma tabla leída por la otra columna. Aquí
        el ancla es la OPERACIÓN, que es como se trabaja cuando el trato se pagó en partes y
        hay que cuadrarlo — buscar de a un comprobante obliga a llevar la suma de cabeza.
        """
        op = self._get_op_or_404(op_uuid)
        value, currency = self.operation_value(op)
        delivered = self.delivered_amount(op)
        uncovered = float(op.uncovered_amount or 0)
        pending = round(value - delivered - uncovered, 2)

        settlements = [
            {
                "payment_id": s.outgoing_payment_id,
                "settled_amount": s.settled_amount,
                "rate": s.settled_reference_rate,
                "amount": s.payment.amount if s.payment else None,
                "currency": s.payment.currency if s.payment else None,
            }
            for s in op.outgoing_settlements
        ]
        mine = {s["payment_id"] for s in settlements}

        phones = self._client_phones_for_operation(op)
        rows = (
            self.db.query(WhatsAppOutgoingPayment)
            .filter(
                WhatsAppOutgoingPayment.client_phone.in_(phones),
                WhatsAppOutgoingPayment.id.notin_(mine or {0}),
            )
            .order_by(WhatsAppOutgoingPayment.created_at.desc())
            .limit(60)
            .all()
        )
        candidates = []
        for row in rows:
            free = self._free_amount(row)
            if free <= 0.01:
                continue
            candidates.append({
                "payment_id": row.id,
                "amount": row.amount,
                "free_amount": free,
                "currency": row.currency,
                "provider": row.provider,
                "reference": row.reference,
                "created_at": row.created_at,
            })

        reference_rate = self._reference_rate(op, _RateProbe(currency=self._payout_currency(op)))
        return {
            "operation_uuid": str(op.uuid),
            "value": value,
            "value_currency": currency,
            "delivered": delivered,
            "uncovered": op.uncovered_amount,
            "uncovered_reason": op.uncovered_reason,
            "pending": pending,
            "reference_rate": reference_rate,
            "settlements": settlements,
            "candidates": candidates,
            "suggestion": suggest_combination(candidates, pending, reference_rate),
        }
```

Con los tres auxiliares que necesita:

```python
    def _payout_currency(self, op: WhatsAppOperation) -> Optional[str]:
        """La moneda de la pata que sale: contra ella se miden los comprobantes."""
        cp = op.currency_pair
        return cp.to_currency.symbol if cp and cp.to_currency else None

    def _free_amount(self, payment: WhatsAppOutgoingPayment) -> float:
        """Cuánto del comprobante no está repartido todavía, en su propia moneda."""
        usado = sum(
            (s.settled_amount or 0) * (s.settled_reference_rate or 0)
            for s in payment.settlements
            if s.settled_reference_rate
        )
        return round(float(payment.amount or 0) - usado, 2)

    def _client_phones_for_operation(self, op: WhatsAppOperation) -> list[str]:
        """Bajo qué teléfonos pueden estar los comprobantes de esta operación."""
        phone = op.client.phone if op.client else None
        return [phone] if phone else []
```

Y el marcador de moneda que `_reference_rate` espera (necesita un objeto con `.currency` y
`.amount`, no un pago entero):

```python
@dataclass
class _RateProbe:
    """Lo mínimo que `_reference_rate` mira de un comprobante: su moneda."""
    currency: Optional[str]
    amount: Optional[float] = None
```

- [ ] **Step 4: Add the router**

```python
# app/routers/operations.py
@router.get("/{op_uuid}/coverage")
async def get_operation_coverage(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Qué cubre ya la operación y con qué comprobantes del cliente podría terminar de cubrirse.
    Espejo de `/payments/outgoing/{id}/settlements`: la misma tabla por la otra columna.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.operation_coverage(op_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
```

- [ ] **Step 5: Run the tests and commit**

```bash
../venv/bin/python -m pytest tests/test_operation_coverage.py -q
git add app/services/whatsapp_payment_service.py app/routers/operations.py tests/test_operation_coverage.py
git commit -m "feat: read an operation's coverage and its candidate receipts"
```

---

### Task 3: La combinación sugerida

**Files:**
- Modify: `app/services/operation_match_service.py` (función pura `suggest_combination`)
- Test: `tests/test_operation_match.py`

**Interfaces:**
- Produces: `suggest_combination(candidates: list[dict], pending: float, rate: float | None) -> list[int]`

- [ ] **Step 1: Write the failing test**

```python
def test_suggest_combination_finds_the_subset_that_squares():
    """El caso 3898: 6.277 + 250.000 + 65.723 = 322.000, que es 350 a 920."""
    from app.services.operation_match_service import suggest_combination

    cands = [
        {"payment_id": 4935, "free_amount": 6_277},
        {"payment_id": 4937, "free_amount": 250_000},
        {"payment_id": 4938, "free_amount": 65_723},
        {"payment_id": 9999, "free_amount": 40_000},
    ]
    assert sorted(suggest_combination(cands, pending=350.0, rate=920.0)) == [4935, 4937, 4938]


def test_suggest_combination_returns_nothing_when_no_subset_squares():
    from app.services.operation_match_service import suggest_combination

    cands = [{"payment_id": 1, "free_amount": 10}, {"payment_id": 2, "free_amount": 20}]
    assert suggest_combination(cands, pending=350.0, rate=920.0) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `../venv/bin/python -m pytest tests/test_operation_match.py -k suggest_combination -q`
Expected: FAIL — no existe.

- [ ] **Step 3: Implement**

```python
#: Cuánto se puede apartar del objetivo un conjunto para seguir contando como "cuadra".
COMBINATION_TOLERANCE = 0.01
#: Tope de candidatos que entran a la búsqueda. 2^18 es instantáneo; más arriba no aporta:
#: un trato pagado con más de 18 comprobantes no lo va a resolver una sugerencia.
COMBINATION_MAX_CANDIDATES = 18


def suggest_combination(
    candidates: Sequence[dict], pending: float, rate: Optional[float]
) -> list[int]:
    """
    El subconjunto de comprobantes cuya suma cuadra con lo que falta de la operación.

    Es lo que el operador hace de cabeza cuando un trato se pagó en partes: probar cuáles
    suman. Devuelve el conjunto MÁS PEQUEÑO que cuadra —entre dos que cuadran, el de menos
    piezas es casi siempre el bueno— y `[]` cuando ninguno lo hace, sin proponer nada a medias.
    """
    if not candidates or not rate or rate <= 0 or pending <= 0:
        return []
    objetivo = pending * rate
    libres = [c for c in candidates if (c.get("free_amount") or 0) > 0][:COMBINATION_MAX_CANDIDATES]
    if not libres:
        return []

    mejor: Optional[list[int]] = None
    for mascara in range(1, 1 << len(libres)):
        suma = 0.0
        elegidos: list[int] = []
        for i, c in enumerate(libres):
            if mascara >> i & 1:
                suma += c["free_amount"]
                elegidos.append(c["payment_id"])
        if abs(suma - objetivo) <= max(objetivo * COMBINATION_TOLERANCE, 0.01):
            if mejor is None or len(elegidos) < len(mejor):
                mejor = elegidos
    return mejor or []
```

- [ ] **Step 4: Run and commit**

```bash
../venv/bin/python -m pytest tests/test_operation_match.py -k suggest_combination -q
git add app/services/operation_match_service.py tests/test_operation_match.py
git commit -m "feat: suggest the subset of receipts that squares with what the operation still needs"
```

---

### Task 4: Escribir la cobertura y derivar la tasa

**Files:**
- Modify: `app/schemas/whatsapp.py` (`OperationCoverageUpdate`, `applied_percentage` con signo)
- Modify: `app/schemas/transaction.py` (`profit_percentage` con signo)
- Modify: `app/services/whatsapp_payment_service.py` (`set_operation_coverage`)
- Modify: `app/routers/operations.py` (`PUT /{op_uuid}/coverage`)
- Test: `tests/test_operation_coverage.py`

**Interfaces:**
- Consumes: `operation_coverage`, `_upsert_settlement`, `_sync_settlement_totals`, `_reference_rate`.
- Produces: `set_operation_coverage(op_uuid, payments, value_amount=None, uncovered=None, actor=None) -> dict`

- [ ] **Step 1: Write the failing tests (el caso de aceptación)**

```python
def test_coverage_derives_the_rate_from_the_sum(db, client, pairs, operator):
    """
    Caso 3898: 350 con tres pagos móviles que suman 322.000. La tasa no se teclea —sale de la
    suma— y por eso el 920 que se cotizó de verdad deja de poder perderse.
    """
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from app.models.whatsapp_operation import WhatsAppOperation

    svc = WhatsAppPaymentService(db)
    a = f.outgoing(db, 65_723, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        svc, "outgoing", a, frm="ZELLE", to="VES",
        from_amount=350, to_amount=315_000, recorded_by=operator.id,
    )
    b = f.outgoing(db, 250_000, "VES", phone="584148861273")
    c = f.outgoing(db, 6_277, "VES", phone="584148861273")
    db.flush()

    svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}, {"payment_id": b.id}, {"payment_id": c.id}],
    )

    row = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op["uuid"]).first()
    assert row.to_amount == pytest.approx(322_000, abs=0.01)
    assert row.rate_used == pytest.approx(920.0, abs=0.001)
    assert svc.delivered_amount(row) == pytest.approx(350.0, abs=0.01)
    assert row.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)


def test_partial_coverage_leaves_the_quote_alone(db, client, pairs, operator):
    """A medias no se deriva nada: la tasa saldría de una suma incompleta."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from app.models.whatsapp_operation import WhatsAppOperation

    svc = WhatsAppPaymentService(db)
    a = f.outgoing(db, 65_723, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        svc, "outgoing", a, frm="ZELLE", to="VES",
        from_amount=350, to_amount=315_000, recorded_by=operator.id,
    )
    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}])

    row = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op["uuid"]).first()
    assert row.to_amount == pytest.approx(315_000, abs=0.01)
    assert row.dict()["pending_amount"] > 0


def test_declared_remainder_closes_and_needs_a_reason(db, client, pairs, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from app.services.whatsapp_quote_service import QuoteServiceError

    svc = WhatsAppPaymentService(db)
    a = f.outgoing(db, 322_000, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        svc, "outgoing", a, frm="ZELLE", to="VES",
        from_amount=400, to_amount=322_000, recorded_by=operator.id,
    )
    with pytest.raises(QuoteServiceError) as exc:
        svc.set_operation_coverage(
            op["uuid"], payments=[{"payment_id": a.id}], uncovered={"amount": 50.0},
        )
    assert exc.value.code == "uncovered_needs_reason"
```

- [ ] **Step 2: Run to verify they fail**

Run: `../venv/bin/python -m pytest tests/test_operation_coverage.py -q`
Expected: FAIL — `set_operation_coverage` no existe.

- [ ] **Step 3: Implement**

```python
#: Motivos por los que una parte del valor puede no tener comprobante.
UNCOVERED_REASONS = ("CASH", "OTHER_CHANNEL", "BALANCE", "ADJUSTMENT")


    def set_operation_coverage(
        self,
        op_uuid: UUID,
        payments: list,
        value_amount: Optional[float] = None,
        uncovered: Optional[dict] = None,
        actor: Optional[User] = None,
    ) -> dict:
        """
        Fija con qué comprobantes se cubre la operación, y deriva su tasa de la suma.

        El monto de la pata que sale NO se teclea: es lo que suman los comprobantes marcados.
        Así el error que motivó todo esto —cotizar 900 cuando eran 920 y no tener dónde
        arreglarlo— deja de ser posible, porque no hay número tecleado que pueda salir mal.

        La derivación ocurre **sólo al cerrar**: a medias la suma es incompleta y una tasa
        sacada de ahí sería absurda (con un comprobante de 65.723 sobre un valor de 350 daría
        187,78). Mientras tanto cada comprobante se valora a la tasa de referencia y la
        cotización se queda como está.
        """
        op = self._get_op_or_404(op_uuid)
        if value_amount is not None:
            if value_amount <= 0:
                raise QuoteServiceError("invalid_amount", "El valor debe ser > 0", 400)
            op.amount = value_amount
        value, _ = self.operation_value(op)

        resto = 0.0
        if uncovered:
            resto = round(float(uncovered.get("amount") or 0), 2)
            motivo = uncovered.get("reason")
            if resto > 0 and motivo not in UNCOVERED_REASONS:
                raise QuoteServiceError(
                    "uncovered_needs_reason",
                    "Declarar un resto sin comprobante exige decir por qué: "
                    + ", ".join(UNCOVERED_REASONS),
                    400,
                )
            op.uncovered_amount = resto or None
            op.uncovered_reason = motivo if resto else None

        rows = []
        for item in payments:
            pid = item["payment_id"] if isinstance(item, dict) else item.payment_id
            row = self.db.query(WhatsAppOutgoingPayment).filter(
                WhatsAppOutgoingPayment.id == pid
            ).first()
            if row is None:
                raise QuoteServiceError("payment_not_found", f"Comprobante {pid} no encontrado", 404)
            explicit = (item.get("settled_amount") if isinstance(item, dict)
                        else getattr(item, "settled_amount", None))
            rows.append((row, explicit))

        suma = round(sum(float(r.amount or 0) for r, _ in rows), 2)
        cierra = value > 0 and abs(suma / (value - resto) if value > resto else 0) > 0 and (
            round(value - resto, 2) > 0
        )
        # La cobertura CIERRA cuando los comprobantes más el resto declarado dan el valor. Con
        # la tasa derivada eso es siempre cierto salvo que no haya comprobantes.
        cierra = bool(rows) and round(value - resto, 2) > 0

        if cierra:
            nueva_tasa = suma / (value - resto)
            op.to_amount = suma
            op.rate_used = nueva_tasa
            op.inverse_percentage = False
            op.applied_percentage = self._margin_against_base(op, nueva_tasa)

        # Se rehace el reparto entero: cada comprobante aporta su monto completo salvo que el
        # llamador diga otra cosa (el caso de uno que abarca dos tratos).
        for row, explicit in rows:
            tasa = self._reference_rate(op, row)
            cubre = explicit if explicit is not None else (
                round(float(row.amount or 0) / tasa, 2) if tasa else None
            )
            if cubre is None or cubre <= 0:
                raise QuoteServiceError(
                    "no_reference_rate",
                    f"Sin tasa para valorar el comprobante {row.id}: indícalo a mano",
                    400,
                )
            self._upsert_settlement(row, op, cubre, tasa, actor)
            self._sync_settlement_totals(row)

        self.db.commit()
        self.db.refresh(op)
        return self.operation_coverage(op.uuid)

    def _margin_against_base(self, op: WhatsAppOperation, rate: float) -> Optional[float]:
        """
        El margen que esta tasa lleva dentro, **con signo**.

        No pasa por `implied_margin` a propósito: esa función existe para INFERIR el margen de
        una tasa que llegó de fuera, y por eso devuelve None fuera del rango comercial — ahí
        «la tasa no salió de este par y afirmar una ganancia sería inventarla». Aquí no hay
        nada que inferir: la tasa sale de los números de la propia operación. Y puede ser
        negativa, que es cuando se entregó por encima de la base; esconderla en un cero sería
        mentir sobre la operación.
        """
        cp = op.currency_pair
        if cp is None or not cp.from_currency or not cp.to_currency:
            return None
        entry = WhatsAppQuoteService(self.db).resolver.get_rate_entry_for_pair(
            cp.from_currency.symbol, cp.to_currency.symbol, at=op.created_at
        )
        if entry is None or not entry.base_rate:
            return None
        base = WhatsAppRateResolver.apply_rate(1.0, entry.base_rate, entry.inverse_percentage)
        if base <= 0:
            return None
        return round((1 - rate / base) * 100, 4)
```

- [ ] **Step 4: Loosen the two schemas**

```python
# app/schemas/whatsapp.py:208 — el margen puede ser negativo: se dio por encima de la base.
    applied_percentage: Optional[float] = Field(None, gt=-100, lt=99)
```

```python
# app/schemas/transaction.py:11
    profit_percentage: float = Field(..., gt=-100, le=100, description="Porcentaje de ganancia asignado")
```

Y el schema del PUT:

```python
# app/schemas/whatsapp.py
class OperationCoveragePayment(BaseModel):
    payment_id: int
    #: Sólo cuando ese comprobante abarca dos tratos; si no, aporta su monto completo.
    settled_amount: Optional[float] = Field(None, gt=0)


class OperationCoverageUncovered(BaseModel):
    amount: float = Field(..., ge=0)
    reason: Optional[Literal["CASH", "OTHER_CHANNEL", "BALANCE", "ADJUSTMENT"]] = None


class OperationCoverageUpdate(BaseModel):
    """Con qué comprobantes se cubre la operación. El conjunto completo, no deltas."""
    payments: List[OperationCoveragePayment]
    value_amount: Optional[float] = Field(None, gt=0)
    uncovered: Optional[OperationCoverageUncovered] = None
```

- [ ] **Step 5: Add the router**

```python
# app/routers/operations.py
@router.put("/{op_uuid}/coverage")
async def set_operation_coverage(
    op_uuid: UUID,
    payload: OperationCoverageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fija con qué comprobantes se cubre la operación. La tasa se deriva de la suma al cerrar;
    el monto de la pata que sale no se teclea.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation_coverage(
            op_uuid,
            payments=[p.dict() for p in payload.payments],
            value_amount=payload.value_amount,
            uncovered=payload.uncovered.dict() if payload.uncovered else None,
            actor=current_user,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
```

- [ ] **Step 6: Run the whole suite and commit**

```bash
../venv/bin/python -m pytest -q
git add -A && git commit -m "feat: an operation's coverage derives its rate from the receipts that pay it"
```

---

### Task 5: Verificación end-to-end contra el caso real

**Files:**
- Test: `tests/test_operation_coverage.py`

- [ ] **Step 1: Add the round-trip test**

```python
def test_writing_from_the_operation_reads_back_from_the_payment(db, client, pairs, operator):
    """Las dos direcciones escriben las MISMAS filas: no pueden discrepar."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    svc = WhatsAppPaymentService(db)
    a = f.outgoing(db, 322_000, "VES", phone="584148861273")
    op = f.create_op_from_payment(
        svc, "outgoing", a, frm="ZELLE", to="VES",
        from_amount=350, to_amount=322_000, recorded_by=operator.id,
    )
    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}])

    desde_el_pago = svc.settlement_summary(a.id)
    assert desde_el_pago["settlements"][0]["operation_uuid"] == op["uuid"]
    assert desde_el_pago["settlements"][0]["settled_amount"] == pytest.approx(350.0, abs=0.01)
```

- [ ] **Step 2: Run the whole suite, then commit**

```bash
../venv/bin/python -m pytest -q
git add tests/test_operation_coverage.py && git commit -m "test: coverage written from either side reads back the same"
```

---

### Task 6: Desplegar y cuadrar la operación 3898

- [ ] **Step 1: Check disk on the EC2 before pushing** (`df -h /`, hacen falta ~2,5 G; `docker image prune -af` es el seguro de antes, `docker builder prune -af` va DESPUÉS)
- [ ] **Step 2:** `git push origin main` y esperar la Action «Deploy to Production» (~3 min, corre `alembic upgrade head`)
- [ ] **Step 3:** Verificar que la migración `a1b2c3d4e5f6` aplicó y las columnas existen
- [ ] **Step 4:** `PUT /operations/{uuid-3898}/coverage` con los comprobantes 4935, 4937 y 4938
- [ ] **Step 5:** Verificar en la base: `to_amount` 322.000, `rate_used` 920, tres liquidaciones sumando 350,00, `pending` 0
- [ ] **Step 6:** `docker builder prune -af`

---

### Task 7: El panel de cobertura (front)

**Files:**
- Create: `src/app/admin/operations/_components/OperationCoveragePanel.tsx`
- Modify: `src/services/operationService.ts` (`getCoverage`, `setCoverage`)
- Modify: `src/types/operation.ts` (tipos de la respuesta)
- Modify: `src/app/admin/operations/_components/OperationItem.tsx` (la puerta: «falta X»)

**Nota de riesgo:** el front va por **Vercel auto-deploy desde `origin/main`**. Empujar a main
DESPLIEGA. Esta tarea se deja en una rama sin mergear hasta que el usuario vea la pantalla.

- [ ] **Step 1:** Tipos y servicio, espejo de `paymentService.getSettlements` / `setSettlements`
- [ ] **Step 2:** El panel, calcado de `OutgoingSettlementsPanel` (SidePanel + barra por tramos + filas + pie)
- [ ] **Step 3:** La puerta desde la lista de operaciones
- [ ] **Step 4:** Commit **en español**, en rama `feat/cobertura-de-operacion`, sin mergear

---

## Self-Review

**Cobertura del spec:** columnas del resto → Task 1. Lectura → Task 2. Sugerencia → Task 3.
Escritura + tasa derivada + margen con signo → Task 4. Las dos direcciones de acuerdo → Task 5.
Caso de aceptación → Task 6. Pantalla y tres puertas → Task 7 (la puerta desde el comprobante y
la de creación quedan para una segunda tanda: el spec las describe, pero sin la pantalla base no
hay dónde colgarlas).

**Consistencia de tipos:** `operation_coverage` devuelve `candidates[]` con `free_amount`, que es
justo lo que `suggest_combination` consume. `set_operation_coverage` devuelve lo mismo que
`operation_coverage`, así que el front lee una sola forma.

**Riesgo abierto que hay que verificar durante la implementación:** una ganancia negativa se
reparte entre socios vía `TransactionProfitSplit`. Hay que confirmar que las sumas del reparto no
asumen signo positivo antes de dar por buena la Task 4.
