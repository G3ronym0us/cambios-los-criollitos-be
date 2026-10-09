"""
Recotizar una operación con otro par, contra Postgres real.

El bot cotiza con el par por defecto del cliente cuando el mensaje no dice la moneda; si era
otro, al vincular el operador lo cambia y la operación debe quedar como si se hubiera
cotizado con el par correcto — no sólo con otra etiqueta.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.schemas.whatsapp import WhatsAppOperationCreate
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests import factories as f


def _quote(db, phone, amount=100, side="SEND"):
    return WhatsAppQuoteService(db).create_quote(WhatsAppOperationCreate(
        client_phone=phone, from_currency="ZELLE", to_currency="VES",
        amount=amount, amount_side=side,
    ))


def test_requote_keeps_the_clients_amount_and_uses_the_new_pairs_rate(
    db, pairs, client, operator
):
    op = _quote(db, client.phone)
    assert op.to_amount == pytest.approx(78292)

    WhatsAppQuoteService(db).requote_with_pair(op.uuid, pairs["ZELLE-COP"].uuid, operator)

    db.refresh(op)
    assert op.currency_pair_id == pairs["ZELLE-COP"].id
    assert op.from_amount == 100
    assert op.to_amount == pytest.approx(76360)
    assert op.rate_used == pytest.approx(763.6)
    assert op.applied_percentage == 8


def test_receive_side_recalculates_what_the_client_sends(db, pairs, client, operator):
    op = _quote(db, client.phone, amount=78292, side="RECEIVE")
    assert op.from_amount == pytest.approx(100)

    WhatsAppQuoteService(db).requote_with_pair(op.uuid, pairs["ZELLE-COP"].uuid, operator)

    db.refresh(op)
    assert op.to_amount == pytest.approx(78292)
    assert op.from_amount == pytest.approx(78292 / 763.6)


def test_dry_run_changes_nothing(db, pairs, client, operator):
    op = _quote(db, client.phone)
    preview = WhatsAppQuoteService(db).requote_with_pair(
        op.uuid, pairs["ZELLE-COP"].uuid, operator, dry_run=True
    )

    db.refresh(op)
    assert op.currency_pair_id == pairs["ZELLE-VES"].id
    assert preview["to_amount"] == pytest.approx(76360)
    assert preview["previous"]["pair_symbol"] == "ZELLE-VES"


def test_the_old_pairs_default_fund_goes_away_with_it(db, pairs, client, operator, fund):
    pairs["ZELLE-VES"].default_fund_in_id = fund.id
    db.flush()
    op = _quote(db, client.phone)
    assert op.fund_group_id == fund.id

    WhatsAppQuoteService(db).requote_with_pair(op.uuid, pairs["ZELLE-COP"].uuid, operator)

    db.refresh(op)
    assert op.fund_group_id is None


def test_refuses_when_the_side_that_changes_currency_has_payments(
    db, pairs, client, operator
):
    op = _quote(db, client.phone)
    out = f.outgoing(db, 78292, "VES", phone=client.phone)
    WhatsAppPaymentService(db).set_operation("outgoing", out.id, op.uuid, completing_user=None)

    with pytest.raises(QuoteServiceError) as exc:
        WhatsAppQuoteService(db).requote_with_pair(op.uuid, pairs["ZELLE-COP"].uuid, operator)
    assert exc.value.code == "requote_side_has_payments"


def test_refuses_a_completed_operation(db, pairs, client, operator):
    op = _quote(db, client.phone)
    op.status = op.status.__class__.COMPLETED
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        WhatsAppQuoteService(db).requote_with_pair(op.uuid, pairs["ZELLE-COP"].uuid, operator)
    assert exc.value.code == "requote_closed_operation"
    assert db.query(WhatsAppOperation).get(op.id).currency_pair_id == pairs["ZELLE-VES"].id
