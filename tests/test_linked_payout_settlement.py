"""
Un saliente que el bot registra ya vinculado a su operación tiene que cubrirla, y en un par
de efectivo el bot no puede cerrarla por haber pagado nuestra pata. Contra Postgres real.

El caso (op 5091 de Neurys, USD-VES, 2026-09-17): el saliente 6084 llegó con FK pero sin fila
de reparto —«Entregado 0,00 de 15», «sin comprobante» en la cuenta— y la operación quedó
COMPLETED en el mismo segundo con el efectivo sin cobrar.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.schemas.whatsapp import (
    WhatsAppOperationComplete,
    WhatsAppOperationCreate,
    WhatsAppPaymentCreate,
)
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f
from tests.conftest import _pair


def _quote(db, phone, frm="ZELLE", amount=100):
    return WhatsAppQuoteService(db).create_quote(WhatsAppOperationCreate(
        client_phone=phone, from_currency=frm, to_currency="VES",
        amount=amount, amount_side="SEND",
    ))


def _bot_payout(db, op, amount):
    return WhatsAppPaymentService(db).create_payment("outgoing", WhatsAppPaymentCreate(
        client_phone=op.client.phone, amount=amount, currency="VES", provider="pago_movil",
        raw_text="Pago movil", operation_uuid=op.uuid,
    ))


def test_a_payout_created_already_linked_covers_its_operation(db, pairs, client):
    op = _quote(db, client.phone)

    _bot_payout(db, op, op.to_amount)

    db.refresh(op)
    assert WhatsAppPaymentService(db).delivered_amount(op) == pytest.approx(100)
    assert op.delivered_amount == pytest.approx(100)


def test_the_backfill_settles_old_fk_only_payouts(db, pairs, client):
    from app.cli import backfill_outgoing_settlements as cli

    op = _quote(db, client.phone)
    viejo = f.outgoing(db, op.to_amount, "VES", phone=client.phone, whatsapp_operation_id=op.id)
    db.commit()
    assert WhatsAppPaymentService(db).delivered_amount(op) == 0

    service = WhatsAppPaymentService(db)
    assert service._settle_linked_payout(viejo, op) is None
    assert service.delivered_amount(op) == pytest.approx(100)
    # Una segunda pasada no duplica.
    assert service._settle_linked_payout(viejo, op) is None
    assert service.delivered_amount(op) == pytest.approx(100)
    assert callable(cli.run)


def test_the_bot_does_not_close_a_cash_operation_by_paying_our_side(db, pairs, client, operator):
    usd_ves = _pair(db, "USD", "VES", 935.0)
    usd_ves.settles_in_cash = True
    db.flush()
    op = _quote(db, client.phone, frm="USD", amount=15)
    _bot_payout(db, op, op.to_amount)

    out = WhatsAppQuoteService(db).complete_operation(op.uuid, WhatsAppOperationComplete(), operator)

    assert out.status == WhatsAppOperationStatus.PENDING
    assert db.query(WhatsAppOperation).get(op.id).completed_at is None
