"""
Probe (agente 1, campaña de ruptura): ¿el sistema avisa o cotiza con una tasa vieja?

`WhatsAppRateResolver._fetch_latest_active` sólo filtra por `is_active=True` y toma la
más reciente por `created_at`; no hay ningún chequeo de antigüedad en el camino de
cotización (`create_quote` / `get_rate_entry_for_pair`). La única noción de "tasa vieja"
que existe en el backend es `RateAlertRepository.top_unacknowledged_by_deviation`
(`app/repositories/rate_alert_repository.py`), y esa sólo se calcula para pares que
tienen una `RateAlert` sin reconocer (divergencia manual vs. automática) -- un par sin
alerta pendiente, con el scraper caído hace una semana, no tiene ninguna señal.

Este test no es un "falla antes, pasa después": documenta que el comportamiento actual
(cotizar sin más, cualquiera sea la antigüedad de la fila activa) es efectivamente el
código tal cual está, para dejar la reproducción trazable. No se toca product code.
"""

from datetime import datetime, timedelta, timezone

from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


def test_get_rate_entry_uses_month_old_active_rate_without_any_signal(db):
    pair = CurrencyPair(
        from_currency_id=_currency(db, "USDT").id,
        to_currency_id=_currency(db, "VES").id,
        pair_symbol="USDT-VES",
        is_active=True,
    )
    db.add(pair)
    db.flush()

    old_rate = ExchangeRate(
        currency_pair_id=pair.id,
        from_currency="USDT",
        to_currency="VES",
        rate=100.0,  # obviamente vieja: cualquier tasa USDT/VES real reciente es varios
                     # cientos/miles de Bs por USDT.
        is_active=True,
    )
    db.add(old_rate)
    db.flush()
    # Simula que el scraper lleva un mes caído y nadie tocó esta fila desde entonces.
    old_rate.created_at = datetime.now(timezone.utc) - timedelta(days=30)
    db.flush()

    entry = WhatsAppRateResolver(db).get_rate_entry_for_pair("USDT", "VES")

    assert entry is not None
    assert entry.rate == 100.0  # se sirve tal cual, sin objeción

    # RateEntry no tiene NINGÚN campo que exprese antigüedad: quien cotiza con esto no
    # tiene forma de saber, desde acá, que la fila lleva 30 días sin refrescar.
    assert not hasattr(entry, "created_at")
    assert not hasattr(entry, "stale_hours")
    assert not hasattr(entry, "age_hours")
