"""
`OperationMatchService` contra Postgres real: el pool de candidatas del lado entrante se
acota al cliente (y sus alias de socio), sabe crear cuando no hay ninguna, y el contrato que
consumen el listado y el cajón de "vincular pago".

Necesita Postgres local (:5433); si no lo hay, estos tests se saltan solos (ver
`tests/conftest.py`).
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationScenario,
    WhatsAppOperationStatus,
)
from app.services.operation_match_service import OperationMatchService
from tests import factories as f


def _client(db, phone, display_name=None, **kw):
    row = WhatsAppClient(phone=phone, display_name=display_name, **kw)
    db.add(row)
    db.flush()
    return row


def _op(db, *, client_id, pair, from_amount, to_amount, status="QUOTED", scenario=None, **kw):
    """Construye una `WhatsAppOperation` a mano (patrón de test_operation_scenario_auto.py):
    `create_op_from_payment` no sirve aquí porque nace ya vinculada."""
    now = kw.pop("created_at", None) or datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client_id,
        currency_pair_id=pair.id,
        from_amount=from_amount,
        to_amount=to_amount,
        rate_used=kw.pop("rate_used", to_amount / from_amount if from_amount else 1.0),
        amount_side=kw.pop("amount_side", WhatsAppAmountSide.SEND),
        status=WhatsAppOperationStatus(status),
        scenario=scenario or WhatsAppOperationScenario.NORMAL,
        created_at=now,
        quoted_at=now,
        expires_at=now + timedelta(minutes=30),
        **kw,
    )
    db.add(op)
    db.flush()
    return op


# ---------------------------------------------------------------------------
# Task 3: el pool de candidatas se acota al cliente
# ---------------------------------------------------------------------------


def test_suggestions_never_reach_another_clients_operation(db, fund, pairs, operator):
    """El caso José Bogao: su comprobante no puede engancharse a la op de Arianna."""
    bogao = _client(db, "584267169499", "Jose Bogao")
    arianna = _client(db, "584128580852", "Arianna")
    _op(db, client_id=arianna.id, pair=pairs["ZELLE-VES"], from_amount=200.0, to_amount=177192.0)
    pago = f.incoming(db, 200.0, "ZELLE", phone=bogao.phone)
    db.flush()

    items = OperationMatchService(db).suggest_for_payments([pago.id], "incoming")

    # Antes de la Task 4 (proponer CREATE) el servicio simplemente no sugiere nada: lo que
    # importa aquí es que la op de Arianna NUNCA aparece.
    assert items == []


def test_a_cash_pair_operation_is_never_suggested_for_an_incoming_receipt(db, fund, pairs, operator):
    from app.models.currency import Currency
    from app.models.currency_pair import CurrencyPair

    usd = Currency(symbol="USD", name="USD")
    db.add(usd)
    db.flush()
    ves = db.query(Currency).filter(Currency.symbol == "VES").first()
    efectivo = CurrencyPair(
        from_currency_id=usd.id, to_currency_id=ves.id, pair_symbol="USD-VES",
        is_active=True, settles_in_cash=True,
    )
    db.add(efectivo)
    db.flush()

    cliente = _client(db, "584124640125", "Cliente Efectivo")
    _op(db, client_id=cliente.id, pair=efectivo, from_amount=100.0, to_amount=78292.0)
    pago = f.incoming(db, 100.0, "USD", phone=cliente.phone)
    db.flush()

    items = OperationMatchService(db).suggest_for_payments([pago.id], "incoming")
    assert items == []


def test_a_via_partner_operation_is_never_suggested_for_an_incoming_receipt(db, fund, pairs, operator):
    cliente = _client(db, "584124640125", "Cliente Socio")
    _op(
        db, client_id=cliente.id, pair=pairs["ZELLE-VES"], from_amount=100.0, to_amount=78292.0,
        scenario=WhatsAppOperationScenario.VIA_PARTNER,
    )
    pago = f.incoming(db, 100.0, "ZELLE", phone=cliente.phone)
    db.flush()

    items = OperationMatchService(db).suggest_for_payments([pago.id], "incoming")
    assert items == []
