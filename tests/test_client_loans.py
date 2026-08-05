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
