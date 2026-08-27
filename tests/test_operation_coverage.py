"""
La cobertura de una operación: varios comprobantes, una tasa que sale de la suma.

El caso que lo motiva es la operación 3898 — 350 pagados con TRES pagos móviles (6.277,
250.000 y 65.723 bolívares, que suman 322.000 = 350 a 920). Se armó desde el primero, quedó
cotizada a 900 y los otros dos no volvían a verla entre las sugeridas.

Ver `docs/superpowers/specs/2026-08-27-cobertura-de-operacion-design.md`.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f

TELEFONO = "584148861273"


@pytest.fixture
def svc(db):
    return WhatsAppPaymentService(db)


def _op(svc, db, pago, *, valor=350, salida=315_000, operator=None):
    """Una operación nacida de un comprobante de salida, como la arma el operador."""
    return f.create_op_from_payment(
        svc, "outgoing", pago, frm="ZELLE", to="VES",
        from_amount=valor, to_amount=salida, recorded_by=operator.id,
    )


def _row(db, op_dict) -> WhatsAppOperation:
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_dict["uuid"]).first()


# ---------------------------------------------------------------------------
# El resto declarado
# ---------------------------------------------------------------------------


def test_a_declared_remainder_closes_the_pending(svc, db, client, pairs, operator):
    """Declarar el resto es lo que deja cerrar un trato que los comprobantes no cubren entero."""
    pago = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, pago, salida=322_000, operator=operator)
    row = _row(db, op)
    # El trato vale 400 pero sus comprobantes sólo dan 350: los otros 50 fueron por fuera.
    row.amount = 400
    db.flush()

    assert row.dict()["pending_amount"] == pytest.approx(50.0, abs=0.01)
    row.uncovered_amount = 50.0
    row.uncovered_reason = "OTHER_CHANNEL"
    db.flush()
    assert row.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Leer la cobertura desde la operación
# ---------------------------------------------------------------------------


def test_coverage_lists_the_clients_free_receipts(svc, db, client, pairs, operator):
    """Los candidatos son los salientes del mismo cliente que todavía tienen saldo libre."""
    primero = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, primero, operator=operator)
    f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    f.outgoing(db, 999, "VES", phone="573000000000")
    db.flush()

    cov = svc.operation_coverage(op["uuid"])

    assert cov["value"] == pytest.approx(350.0)
    assert {c["amount"] for c in cov["candidates"]} == {250_000, 6_277}
    assert [s["payment_id"] for s in cov["settlements"]] == [primero.id]


# ---------------------------------------------------------------------------
# Escribir la cobertura — el caso de aceptación
# ---------------------------------------------------------------------------


def test_coverage_derives_the_rate_from_the_sum(svc, db, client, pairs, operator):
    """
    Caso 3898 al centavo: la tasa no se teclea, sale de la suma. Por eso el 920 que se cotizó
    de verdad deja de poder perderse en un número mal escrito.
    """
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)
    b = f.outgoing(db, 250_000, "VES", phone=TELEFONO)
    c = f.outgoing(db, 6_277, "VES", phone=TELEFONO)
    db.flush()

    svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}, {"payment_id": b.id}, {"payment_id": c.id}],
    )

    row = _row(db, op)
    assert row.to_amount == pytest.approx(322_000, abs=0.01)
    assert row.rate_used == pytest.approx(920.0, abs=0.001)
    assert svc.delivered_amount(row) == pytest.approx(350.0, abs=0.01)
    assert row.dict()["pending_amount"] == pytest.approx(0.0, abs=0.01)


def test_partial_coverage_leaves_the_quote_alone(svc, db, client, pairs, operator):
    """A medias no se deriva nada: 65.723 sobre un valor de 350 daría una tasa de 187,78."""
    a = f.outgoing(db, 65_723, "VES", phone=TELEFONO)
    op = _op(svc, db, a, operator=operator)
    db.flush()

    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}], partial=True)

    row = _row(db, op)
    assert row.to_amount == pytest.approx(315_000, abs=0.01)
    assert row.rate_used == pytest.approx(900.0, abs=0.001)
    assert row.dict()["pending_amount"] > 0


def test_a_declared_remainder_needs_a_reason(svc, db, client, pairs, operator):
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, valor=400, salida=322_000, operator=operator)
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        svc.set_operation_coverage(
            op["uuid"], payments=[{"payment_id": a.id}], uncovered={"amount": 50.0},
        )
    assert exc.value.code == "uncovered_needs_reason"


def test_the_remainder_is_stored_with_its_reason(svc, db, client, pairs, operator):
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, valor=400, salida=322_000, operator=operator)
    db.flush()

    cov = svc.set_operation_coverage(
        op["uuid"],
        payments=[{"payment_id": a.id}],
        uncovered={"amount": 50.0, "reason": "OTHER_CHANNEL"},
    )

    assert cov["uncovered"] == pytest.approx(50.0)
    assert cov["uncovered_reason"] == "OTHER_CHANNEL"
    assert cov["pending"] == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Las dos direcciones escriben lo mismo
# ---------------------------------------------------------------------------


def test_writing_from_the_operation_reads_back_from_the_payment(svc, db, client, pairs, operator):
    """Son las MISMAS filas leídas por la otra columna: no pueden discrepar."""
    a = f.outgoing(db, 322_000, "VES", phone=TELEFONO)
    op = _op(svc, db, a, salida=322_000, operator=operator)
    db.flush()

    svc.set_operation_coverage(op["uuid"], payments=[{"payment_id": a.id}])

    desde_el_pago = svc.settlement_summary(a.id)
    assert str(desde_el_pago["settlements"][0]["operation_uuid"]) == str(op["uuid"])
    assert desde_el_pago["settlements"][0]["settled_amount"] == pytest.approx(350.0, abs=0.01)
