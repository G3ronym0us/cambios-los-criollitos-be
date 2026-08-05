"""Préstamos a entidades: deudor explícito, alta manual y totales."""

from datetime import datetime, timedelta, timezone

import pytest

import app.services.bcv_service as bcv_service
from app.models.bcv_rate import BcvRate
from app.models.client_loan import ClientLoan, ClientLoanPreferredValue, ClientLoanStatus
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.whatsapp_client import WhatsAppClient
from app.services.client_entity_service import ClientEntityService
from app.services.client_loan_service import ClientLoanService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories


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


def _currency(db, symbol: str) -> Currency:
    """Get-or-create, igual que el helper homónimo de conftest — no se importa de ahí
    porque es privado del módulo; se duplica en miniatura para no crear una dependencia
    entre archivos de test."""
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


@pytest.fixture
def ves_rates(db):
    """
    Tasas para valorar bolívares: VES/USDT del negocio y la oficial del BCV.

    Ojo con la dirección: el par va FIAT→USDT y `rate` son bolívares por USDT, con
    `inverse_percentage=True` para que aplicarlo sea dividir. Es la convención del sistema
    (ver `WhatsAppRateResolver._get_direct_entry` y `valuation.historical_rate`); guardarlo
    al revés hace que las dos rutas de conversión den resultados distintos.
    """
    # `currency_pair_id` es NOT NULL en el esquema real (a diferencia del fake de
    # test_whatsapp_rate_resolver.py, que corre contra SQLite sin esa FK); el resolver
    # solo mira from_currency/to_currency, así que el par en sí es incidental.
    pair = CurrencyPair(
        from_currency_id=_currency(db, "VES").id,
        to_currency_id=_currency(db, "USDT").id,
        pair_symbol="VES-USDT",
        is_active=True,
    )
    db.add(pair)
    db.flush()

    # Fechadas en el pasado: la valuación histórica solo mira tasas anteriores al momento
    # del préstamo, y los tests valoran a "hace dos horas".
    past = datetime.now(timezone.utc) - timedelta(days=7)
    db.add(ExchangeRate(
        currency_pair_id=pair.id,
        from_currency="VES", to_currency="USDT", rate=500.0,
        inverse_percentage=True, is_active=True, created_at=past,
    ))
    db.add(BcvRate(rate=400.0, fetched_at=past))
    db.flush()
    # `get_cached_bcv_rate` cachea en una global del módulo; sin limpiarla, la tasa de un
    # test se cuela en el siguiente.
    bcv_service._cached_rate = None
    bcv_service._cached_expiry = 0.0


@pytest.fixture
def entity(db):
    return ClientEntityService(db).create("Bodegón X", "120363000000000000@g.us")


def test_loan_from_group_payment_requires_a_borrower(db, ves_rates):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    with pytest.raises(QuoteServiceError) as exc:
        ClientLoanService(db).create_from_outgoing(
            payment_id=payment.id,
            preferred_value="BCV",
            payment_currency="VES",
            fiat_currency="VES",
        )

    assert exc.value.code == "loan_borrower_required"
    assert exc.value.http_status == 400


def test_loan_from_group_payment_uses_the_given_borrower(db, ves_rates, entity):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    loan = ClientLoanService(db).create_from_outgoing(
        payment_id=payment.id,
        preferred_value="BCV",
        payment_currency="VES",
        fiat_currency="VES",
        client_uuid=entity.uuid,
    )

    assert loan["client_uuid"] == entity.uuid
    assert loan["preferred_currency"] == "USD_BCV"
    assert loan["principal_amount"] == pytest.approx(12.5)  # 5000 VES / 400 BCV


def test_loan_from_phone_payment_still_works_without_borrower(db, ves_rates, client):
    payment = factories.outgoing(db, 5000, "VES", phone=client.phone)

    loan = ClientLoanService(db).create_from_outgoing(
        payment_id=payment.id,
        preferred_value="FIAT",
        payment_currency="VES",
        fiat_currency="VES",
    )

    assert loan["client_uuid"] == client.uuid


def test_anonymous_client_cannot_be_a_borrower(db, ves_rates, client):
    anon = WhatsAppClient(phone="anon:group:1", display_name="Anónimo (vía fondo)")
    db.add(anon)
    db.flush()
    payment = factories.outgoing(db, 5000, "VES", phone=client.phone)

    with pytest.raises(QuoteServiceError) as exc:
        ClientLoanService(db).create_from_outgoing(
            payment_id=payment.id,
            preferred_value="FIAT",
            payment_currency="VES",
            fiat_currency="VES",
            client_uuid=anon.uuid,
        )

    assert exc.value.code == "loan_client_invalid"


def test_preview_suggests_the_entity_linked_to_the_group(db, ves_rates, entity):
    payment = factories.outgoing(db, 5000, "VES", phone="120363000000000000@g.us")

    preview = ClientLoanService(db).preview_outgoing(payment.id, "VES", "VES")

    assert preview["requires_borrower"] is True
    assert preview["suggested_client"] == {"uuid": entity.uuid, "display_name": "Bodegón X"}
