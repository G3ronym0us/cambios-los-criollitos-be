"""Constructores de pagos y operaciones para los tests de integración."""

from datetime import datetime, timezone

from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment


def incoming(db, amount, currency="ZELLE", phone="13174961478", **kw):
    row = WhatsAppIncomingPayment(
        client_phone=phone, amount=amount, currency=currency,
        created_at=kw.pop("created_at", datetime.now(timezone.utc)), **kw,
    )
    db.add(row)
    db.flush()
    return row


def outgoing(db, amount, currency, phone="13174961478", **kw):
    row = WhatsAppOutgoingPayment(
        client_phone=phone, amount=amount, currency=currency,
        created_at=kw.pop("created_at", datetime.now(timezone.utc)), **kw,
    )
    db.add(row)
    db.flush()
    return row


def create_op_from_payment(service, table, payment, *, frm, to, from_amount, to_amount,
                           fund_uuid=None, user_uuid=None, recorded_by=None):
    """Crea una operación desde un comprobante (el flujo real del panel). Devuelve op.dict()."""
    return service.create_operation_from_payment(
        table, payment.id, frm, to, from_amount, to_amount,
        fund_group_uuid=fund_uuid, exchange_user_uuid=user_uuid,
        recorded_by_user_id=recorded_by,
    )
