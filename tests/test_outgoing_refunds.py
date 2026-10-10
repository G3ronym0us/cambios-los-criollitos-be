"""
Devoluciones de un saliente pagado de más, contra Postgres real.

El caso (saliente 5917, 2026-09-13): se tecleó en el banco la cifra en COP y salieron 28.900
Bs por una operación COP-VES que pedía muchos menos; el beneficiario devolvió 20.500 Bs
(entrante 636). El pago tiene que contar por su neto y la devolución tener destino.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.whatsapp import WhatsAppOperationCreate
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests import factories as f

PAGADO = 28900.0
DEVUELTO = 20500.0


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, client):
    """COP-VES: 28.884,70 COP → 7.091,19 Bs a 0,2455."""
    return WhatsAppQuoteService(db).create_quote(WhatsAppOperationCreate(
        client_phone=client.phone, from_currency="COP", to_currency="VES",
        amount=28884.70, amount_side="SEND",
    ))


def _pago(db, client, **kw):
    return f.outgoing(db, PAGADO, "VES", phone=client.phone, identification="V9426135", **kw)


def _devolucion(db, client, pago, amount=DEVUELTO, **kw):
    return f.incoming(
        db, amount, "VES", phone=client.phone, identification="V-9426135",
        created_at=pago.created_at + timedelta(minutes=16), **kw,
    )


def test_linking_an_overpayment_no_longer_overcovers_the_operation(service, db, pairs, client):
    """Sin monto explícito, lo que da la tasa se topa en el pendiente."""
    op = _op(db, client)
    pago = _pago(db, client)

    service.set_operation("outgoing", pago.id, op.uuid)

    db.refresh(op)
    value, _ = service.operation_value(op)
    assert service.delivered_amount(op) == pytest.approx(value)
    assert service._free_amount(pago) == pytest.approx(PAGADO - op.to_amount, abs=0.05)


def test_the_returned_incoming_is_offered_and_the_payment_counts_net(service, db, pairs, client):
    pago = _pago(db, client)
    devolucion = _devolucion(db, client, pago)
    # Otro cliente y sin la cédula: no es candidato.
    f.incoming(db, DEVUELTO, "VES", phone="584120000000",
               created_at=pago.created_at + timedelta(minutes=5))

    candidatos = service.refunds_summary(pago.id)["candidates"]
    assert [c["incoming_payment_id"] for c in candidatos] == [devolucion.id]

    out = service.set_refunds(pago.id, [{"incoming_payment_id": devolucion.id}])

    assert out["refunded_amount"] == DEVUELTO
    assert out["net_amount"] == PAGADO - DEVUELTO
    assert out["candidates"] == []
    db.refresh(devolucion)
    assert service._with_name(devolucion)["refund_of_outgoing_id"] == pago.id


def test_the_net_is_what_covers_the_operation(service, db, pairs, client):
    op = _op(db, client)
    pago = _pago(db, client)
    service.set_refunds(pago.id, [{"incoming_payment_id": _devolucion(db, client, pago).id}])

    preview = service.coverage_preview(pago.id, op.uuid)
    assert preview["payment"]["net_amount"] == PAGADO - DEVUELTO
    assert preview["suggested_settled_amount"] == pytest.approx((PAGADO - DEVUELTO) / 0.2455, rel=1e-4)

    # Cuadrar deriva la tasa del NETO, no de los 28.900.
    service.set_operation_coverage(op.uuid, [{"payment_id": pago.id}])
    db.refresh(op)
    assert op.to_amount == pytest.approx(PAGADO - DEVUELTO)


def test_a_refund_incoming_cannot_be_used_for_anything_else(service, db, pairs, client):
    op = _op(db, client)
    pago = _pago(db, client)
    devolucion = _devolucion(db, client, pago)
    service.set_refunds(pago.id, [{"incoming_payment_id": devolucion.id}])

    with pytest.raises(QuoteServiceError) as exc:
        service.set_operation("incoming", devolucion.id, op.uuid)
    assert exc.value.code == "payment_is_refund"
    with pytest.raises(QuoteServiceError) as exc:
        service.set_irrelevant("incoming", devolucion.id, True, None)
    assert exc.value.code == "payment_is_refund"

    # Fuera de la bandeja «por atender».
    page = service.list_payments_page("incoming", 50, 0, None, "ALL", False, attention="ATTENTION")
    assert devolucion.id not in {p["id"] for p in page["items"]}


def test_a_refund_without_receipt_needs_a_note(service, db, pairs, client):
    pago = _pago(db, client)
    with pytest.raises(QuoteServiceError) as exc:
        service.set_refunds(pago.id, [{"amount": 500}])
    assert exc.value.code == "refund_needs_note"

    out = service.set_refunds(pago.id, [{"amount": 500, "note": "Devolvió en efectivo"}])
    assert out["net_amount"] == PAGADO - 500


def test_cannot_refund_more_than_paid_nor_below_what_it_covers(service, db, pairs, client):
    pago = _pago(db, client)
    with pytest.raises(QuoteServiceError) as exc:
        service.set_refunds(pago.id, [{"amount": PAGADO + 1, "note": "x"}])
    assert exc.value.code == "refund_exceeds_payment"

    op = _op(db, client)
    service.set_operation("outgoing", pago.id, op.uuid)  # cubre ~7.091 Bs del pago
    with pytest.raises(QuoteServiceError) as exc:
        service.set_refunds(pago.id, [{"amount": PAGADO - 1000, "note": "x"}])
    assert exc.value.code == "refund_below_settled"


def test_clearing_the_refunds_frees_the_incoming(service, db, pairs, client):
    pago = _pago(db, client)
    devolucion = _devolucion(db, client, pago)
    service.set_refunds(pago.id, [{"incoming_payment_id": devolucion.id}])

    out = service.set_refunds(pago.id, [])

    assert out["net_amount"] == PAGADO
    assert [c["incoming_payment_id"] for c in out["candidates"]] == [devolucion.id]
