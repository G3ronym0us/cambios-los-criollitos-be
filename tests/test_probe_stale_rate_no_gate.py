"""
Probe (agente 1, campaña de ruptura): ¿el sistema avisa o cotiza con una tasa vieja?

`WhatsAppRateResolver._fetch_latest_active` sólo filtra por `is_active=True` y toma la
más reciente por `created_at`; no hay ningún chequeo de antigüedad en el camino de
cotización (`create_quote` / `get_rate_entry_for_pair`). La única noción de "tasa vieja"
que existe en el backend es `RateAlertRepository.top_unacknowledged_by_deviation`
(`app/repositories/rate_alert_repository.py`), y esa sólo se calcula para pares que
tienen una `RateAlert` sin reconocer (divergencia manual vs. automática) -- un par sin
alerta pendiente, con el scraper caído hace una semana, no tiene ninguna señal.

Va como `xfail(strict=True)` afirmando lo que DEBERÍA pasar, no lo que pasa. Es el mismo
patrón de `test_probe_inverse_pair_labeling`, y por el mismo motivo: escrito al revés
—afirmando que los campos de antigüedad NO existen— la prueba congelaba la ausencia del
arreglo, y el día que alguien añadiera la señal se pondría roja por hacerlo bien. Con
`strict`, ese día pasa a XPASS y falla pidiendo que se quite la marca, que es el aviso que
sí queremos. No se toca product code.
"""

from datetime import datetime, timedelta, timezone

import pytest

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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Hueco real, sin arreglar: no hay ninguna puerta de antigüedad en el camino de "
        "cotización y `RateEntry` no expone la edad de la fila. Cuando alguien la añada, "
        "esto pasa a XPASS y hay que quitar la marca."
    ),
)
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

    # Lo que DEBERÍA pasar: quien cotiza tiene que poder saber, desde el propio `RateEntry`,
    # que la fila lleva 30 días sin refrescar. Hoy no hay ningún campo que lo exprese, así
    # que esto falla — y por eso el test va marcado `xfail`.
    edad = next(
        (
            getattr(entry, campo)
            for campo in ("age_hours", "stale_hours", "created_at")
            if hasattr(entry, campo)
        ),
        None,
    )
    assert edad is not None, (
        "RateEntry no expone ninguna noción de antigüedad: se cotiza una tasa de hace 30 "
        "días sin que el consumidor pueda distinguirla de una de hace 30 segundos."
    )
