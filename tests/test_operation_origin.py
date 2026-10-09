"""
De dónde nació una cotización (`WhatsAppOperation.origin`), contra Postgres real.

El caso que lo pide (Nelson, 2026-09-19): manda la captura del Zelle y los datos en Bs, y
el bot cotiza «30 ZELLE». Para el panel eso era una cotización de texto más, y al vincular
no había forma de saber que el monto había salido de un comprobante.
"""

from datetime import datetime, timedelta, timezone

from app.models.whatsapp_operation import WhatsAppOperation
from app.schemas.whatsapp import WhatsAppOperationCreate, WhatsAppPaymentCreate
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f


def _quote(db, phone, amount=30):
    return WhatsAppQuoteService(db).create_quote(WhatsAppOperationCreate(
        client_phone=phone, from_currency="ZELLE", to_currency="VES",
        amount=amount, amount_side="SEND",
    ))


def test_a_text_quote_alone_is_text(db, pairs, client):
    assert _quote(db, client.phone).origin == "TEXT"


def test_a_receipt_just_before_the_quote_makes_it_text_receipt(db, pairs, client):
    f.incoming(db, 30, "ZELLE", phone=client.phone,
               created_at=datetime.now(timezone.utc) - timedelta(minutes=3))
    assert _quote(db, client.phone).origin == "TEXT_RECEIPT"


def test_an_old_receipt_does_not_count(db, pairs, client):
    f.incoming(db, 30, "ZELLE", phone=client.phone,
               created_at=datetime.now(timezone.utc) - timedelta(minutes=30))
    assert _quote(db, client.phone).origin == "TEXT"


def test_another_clients_receipt_does_not_count(db, pairs, client):
    f.incoming(db, 30, "ZELLE", phone="584120000000")
    assert _quote(db, client.phone).origin == "TEXT"


def test_a_receipt_saved_right_after_the_quote_upgrades_it(db, pairs, client):
    op = _quote(db, client.phone)
    assert op.origin == "TEXT"

    WhatsAppPaymentService(db).create_payment("incoming", WhatsAppPaymentCreate(
        client_phone=client.phone, amount=30, currency="ZELLE", provider="zelle",
        raw_text="Zelle 30.00 enviado",
    ))

    db.refresh(op)
    assert op.origin == "TEXT_RECEIPT"


def test_operations_created_from_a_receipt_say_which_side(db, pairs, client, operator):
    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone)
    from_inc = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="VES", from_amount=100, to_amount=78292,
        recorded_by=operator.id)
    out = f.outgoing(db, 78292, "VES", phone=client.phone)
    from_out = f.create_op_from_payment(
        svc, "outgoing", out, frm="ZELLE", to="VES", from_amount=100, to_amount=78292,
        recorded_by=operator.id)

    def origin(created):
        return db.query(WhatsAppOperation).filter(
            WhatsAppOperation.uuid == str(created["uuid"])).one().origin

    assert origin(from_inc) == "INCOMING_RECEIPT"
    assert origin(from_out) == "OUTGOING_RECEIPT"
    assert from_inc["origin"] == "INCOMING_RECEIPT"


def test_an_operation_from_an_old_receipt_is_dated_when_the_money_moved(
    db, pairs, client, operator, fund
):
    """
    Entrante 628 de Nelson (2026-09-12 18:03) convertido en operación el 09-10: salía con la
    fecha de hoy y se iba al final de todo listado.
    """
    from app.models.transaction import Transaction

    when = datetime.now(timezone.utc) - timedelta(days=27)
    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone, created_at=when)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="VES", from_amount=100, to_amount=88596,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)
    op = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(created["uuid"])).one()

    assert op.created_at == when
    assert op.quoted_at == when
    assert op.valuation_at == when
    # No nace vencida: nadie le cotizó con un TTL.
    assert op.expires_at > datetime.now(timezone.utc)
    if op.transaction_id:
        assert db.query(Transaction).get(op.transaction_id).created_at == when


def test_an_operation_from_an_old_payout_is_completed_when_it_was_paid(db, pairs, client, operator):
    when = datetime.now(timezone.utc) - timedelta(days=5)
    svc = WhatsAppPaymentService(db)
    out = f.outgoing(db, 78292, "VES", phone=client.phone, created_at=when)
    created = f.create_op_from_payment(
        svc, "outgoing", out, frm="ZELLE", to="VES", from_amount=100, to_amount=78292,
        recorded_by=operator.id)
    op = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(created["uuid"])).one()

    assert op.status.value == "COMPLETED"
    assert op.created_at == when
    assert op.completed_at == when
