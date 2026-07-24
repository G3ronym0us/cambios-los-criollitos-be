"""
Reglas de vínculo de una operación con sus comprobantes, contra Postgres real. Reemplazan a
los tests mock que se rompían con cada refactor y algunos de los cuales probaban comportamiento
que el modelo por valor cambió a propósito (ej. «una op no admite un segundo saliente»).
"""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def test_operation_accepts_several_payouts(service, db, fund, client, operator):
    """El modelo por valor SÍ admite varios salientes por operación (antes se rechazaba)."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    service.set_operation("outgoing", f.outgoing(db, 914.04, "BRL").id, op.uuid,
                          completing_user=operator, complete_outgoing=True)
    # El segundo saliente ya no da 409: se contabiliza como otra parte del valor.
    res = service.set_operation("outgoing", f.outgoing(db, 15658.4, "VES").id, op.uuid,
                                completing_user=operator, complete_outgoing=True)
    assert str(res["operation_uuid"]) == str(op.uuid)


def test_incoming_operation_stays_pending_until_a_payout(service, db, fund, client, operator):
    """Un entrante inicia la op pero no la completa: falta entregar el dinero al cliente."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.status == WhatsAppOperationStatus.PENDING and op.transaction_id is not None


def test_operation_from_group_receipt_uses_anonymous_client(service, db, fund, pairs, operator):
    """Un comprobante reenviado al grupo no pone al grupo como cliente: queda anónimo."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    pay = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", pay, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.client.phone.startswith("anon:")


def test_linking_outgoing_adopts_the_real_client_of_an_anonymous_op(service, db, fund, pairs, operator):
    """Al vincular el saliente real a una op anónima de grupo, adopta ese teléfono como cliente."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    inc = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.client.phone.startswith("anon:")

    pix = f.outgoing(db, 914.04, "BRL", phone="584127777777")
    service.set_operation("outgoing", pix.id, op.uuid, completing_user=operator, complete_outgoing=True)
    db.refresh(op)
    assert op.client.phone == "584127777777"


def test_operation_inherits_fund_group_of_the_payment_when_omitted(service, db, fund, pairs, operator):
    """Crear la op sin indicar fondo hereda el del comprobante reenviado al grupo."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    pay = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, service.create_operation_from_payment(
        "incoming", pay.id, "ZELLE", "BRL", 200, 914.04,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])
    assert op.fund_group_id == fund.id


def test_convert_outgoing_to_incoming_keeps_operation_and_date(service, db, fund, client, operator):
    """Mover un saliente a la bandeja de entrantes conserva su operación y su fecha."""
    from datetime import datetime, timezone
    when = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    inc = f.incoming(db, 200, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    out = f.outgoing(db, 500, "VES", created_at=when, whatsapp_operation_id=op.id)

    res = service.convert_outgoing_to_incoming(out.id)
    assert str(res["operation_uuid"]) == str(op.uuid)
    assert res["created_at"].replace(tzinfo=timezone.utc) == when
