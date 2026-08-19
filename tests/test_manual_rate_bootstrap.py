"""
Ponerle el primer precio manual a un par recién creado.

`set_manual_rate` solo sabía ACTUALIZAR: al crear la primera tasa armaba el ExchangeRate sin
`currency_pair_id` —que es NOT NULL—, el INSERT reventaba, el except se lo tragaba y el endpoint
respondía «No exchange rate found for pair». Un par nuevo sin scraper, como la paridad
USDT-USDT de la que cuelga ZELLE-USDT, no tenía forma de recibir su tasa desde el panel.
"""

from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.repositories.exchange_rate_repository import ExchangeRateRepository


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def _bare_pair(db, frm: str, to: str) -> CurrencyPair:
    """Par sin ninguna tasa todavía: exactamente lo que deja POST /currency-pairs."""
    pair = CurrencyPair(
        from_currency_id=_currency(db, frm).id,
        to_currency_id=_currency(db, to).id,
        pair_symbol=f"{frm}-{to}",
        is_active=True,
    )
    db.add(pair)
    db.flush()
    return pair


def test_manual_rate_creates_the_first_rate_of_a_pair(db):
    """El caso de la paridad: par nuevo, sin tasa, recibe su precio manual."""
    pair = _bare_pair(db, "USDT", "USDT")

    rate = ExchangeRateRepository(db).set_manual_rate("USDT", "USDT", 1.0)

    assert rate is not None, "un par sin tasa previa tiene que poder recibir la primera"
    assert rate.currency_pair_id == pair.id
    assert rate.rate == 1.0
    assert rate.manual_rate == 1.0
    assert rate.is_manual is True
    assert rate.is_active is True


def test_manual_rate_still_updates_an_existing_rate(db):
    """La ruta de siempre no cambia: si ya hay tasa, se actualiza y se guarda la automática."""
    pair = _bare_pair(db, "ZELLE", "USDT")
    db.add(ExchangeRate(
        currency_pair_id=pair.id, from_currency="ZELLE", to_currency="USDT",
        rate=1.05, is_active=True,
    ))
    db.flush()

    rate = ExchangeRateRepository(db).set_manual_rate("ZELLE", "USDT", 1.0752688)

    assert rate is not None
    assert rate.rate == 1.0752688
    assert rate.automatic_rate == 1.05, "la tasa automática se conserva para poder volver"
    assert rate.is_manual is True


def test_manual_rate_returns_none_when_the_pair_does_not_exist(db):
    """Sin par no hay tasa: se devuelve None, no se crea una fila huérfana."""
    assert ExchangeRateRepository(db).set_manual_rate("VES", "PAYPAL", 123.0) is None
