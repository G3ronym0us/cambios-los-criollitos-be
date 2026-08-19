"""
Un par derivado colgado de una paridad con precio manual.

ZELLE→USDT no tiene mercado: 1 Zelle = 0,93 USDT es política de la casa, un 7% sobre la
paridad. Como todo porcentaje necesita un par base, la paridad se escribe como un par
`USDT-USDT` con precio manual 1 y `ZELLE-USDT` cuelga de ella al 7% inverso.

Lo que se fija acá es el mecanismo: que `_calculate_dynamic_derived_rates` tome las tasas
MANUALES de la base de datos (no solo las que trae Binance en la corrida) y les aplique el
porcentaje del par. Si eso se rompe, ZELLE-USDT no da error: se queda congelado en su último
valor, que es peor.
"""

import pytest

from app.enums.pair_type import PairType
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.services.scrapers.binance_scraper import BinanceP2PScraper
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


@pytest.fixture
def parity_and_derived(db):
    """La paridad USDT-USDT con precio manual 1 y ZELLE-USDT derivado al 7% inverso."""
    parity = CurrencyPair(
        from_currency_id=_currency(db, "USDT").id,
        to_currency_id=_currency(db, "USDT").id,
        pair_symbol="USDT-USDT",
        pair_type=PairType.BASE,
        is_active=True,
        binance_tracked=False,
        is_monitored=False,
    )
    db.add(parity)
    db.flush()
    ExchangeRateRepository(db).set_manual_rate("USDT", "USDT", 1.0)

    derived = CurrencyPair(
        from_currency_id=_currency(db, "ZELLE").id,
        to_currency_id=_currency(db, "USDT").id,
        pair_symbol="ZELLE-USDT",
        pair_type=PairType.DERIVED,
        base_pair_id=parity.id,
        derived_percentage=7,
        use_inverse_percentage=True,
        is_active=True,
    )
    db.add(derived)
    db.flush()
    return parity, derived


def _zelle_usdt(rates):
    return next(r for r in rates if r.from_currency == "ZELLE" and r.to_currency == "USDT")


def test_derived_rate_is_built_from_the_manual_parity(db, parity_and_derived):
    """Sin una sola tasa de Binance en la corrida, la derivada sale de la paridad manual."""
    rates = []

    BinanceP2PScraper(db)._calculate_dynamic_derived_rates(rates, {})

    rate = _zelle_usdt(rates)
    assert rate.rate == pytest.approx(1.0752688, abs=1e-6)  # 1 / (1 - 0,07)
    assert rate.percentage == 7
    assert rate.inverse_percentage is True


def test_a_hundred_zelle_are_ninety_three_usdt(db, parity_and_derived):
    """La cuenta que ve el cliente, con el mismo apply_rate que usan el bot y el front."""
    rates = []

    BinanceP2PScraper(db)._calculate_dynamic_derived_rates(rates, {})

    rate = _zelle_usdt(rates)
    usdt = WhatsAppRateResolver.apply_rate(100.0, rate.rate, rate.inverse_percentage)
    assert usdt == pytest.approx(93.0, abs=0.01)
