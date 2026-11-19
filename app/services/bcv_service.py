"""
Servicio de las tasas oficiales del BCV (USD/VES y EUR/VES).

Port de la lógica en whatsapp-bot/src/rates.ts (fetchBcvRate). La fuente
externa es ve.dolarapi.com. El backend cachea en memoria con TTL 5min y
persiste cada fetch exitoso en `bcv_rates` para auditoría/histórico.

Cada moneda mantiene su propia serie: el euro oficial no se deriva del
dólar oficial, así que las consultas filtran por `currency`.
"""

import time
from typing import Dict, Optional

import httpx
from sqlalchemy.orm import Session

from app.models.bcv_rate import BcvRate


BCV_URLS = {
    "USD": "https://ve.dolarapi.com/v1/dolares/oficial",
    "EUR": "https://ve.dolarapi.com/v1/euros/oficial",
}
BCV_CURRENCIES = tuple(BCV_URLS)
BCV_SOURCE = "ve.dolarapi.com"
BCV_TTL_SECONDS = 5 * 60
BCV_FETCH_TIMEOUT = 10.0


# Cache en memoria del proceso (igual que el bot), una entrada por moneda.
_cached_rates: Dict[str, float] = {}
_cached_expiry: Dict[str, float] = {}


def latest_bcv_rate(db: Session, currency: str = "USD") -> Optional[BcvRate]:
    """Último registro persistido para la moneda pedida (sin fetch HTTP)."""
    return (
        db.query(BcvRate)
        .filter(BcvRate.currency == currency)
        .order_by(BcvRate.fetched_at.desc())
        .first()
    )


def get_cached_bcv_rate(db: Session, currency: str = "USD") -> Optional[float]:
    """
    Retorna la tasa BCV cacheada. Si la cache en memoria expiró, consulta
    el último registro en la tabla `bcv_rates`. No hace fetch HTTP (eso lo
    hace la Celery task `refresh_bcv_rate` o `GET /bcv` cuando no hay nada).
    """
    now = time.time()
    cached = _cached_rates.get(currency)
    if cached is not None and now < _cached_expiry.get(currency, 0.0):
        return cached

    latest = latest_bcv_rate(db, currency)
    if latest is None:
        return None
    _cached_rates[currency] = float(latest.rate)
    _cached_expiry[currency] = now + BCV_TTL_SECONDS
    return float(latest.rate)


async def fetch_bcv_rate(db: Session, currency: str = "USD") -> Optional[float]:
    """
    Hace fetch HTTP a la tasa oficial de la moneda y persiste el resultado.
    Si falla la red, devuelve la última cacheada (o None si no hay).
    """
    url = BCV_URLS.get(currency)
    if url is None:
        raise ValueError(f"Moneda BCV no soportada: {currency}")

    try:
        async with httpx.AsyncClient(timeout=BCV_FETCH_TIMEOUT) as client:
            response = await client.get(url)
            response.raise_for_status()
            data = response.json()
        promedio = data.get("promedio")
        if not isinstance(promedio, (int, float)) or promedio <= 0:
            return get_cached_bcv_rate(db, currency)

        rate = float(promedio)
        db.add(BcvRate(currency=currency, rate=rate, source=BCV_SOURCE))
        db.commit()

        _cached_rates[currency] = rate
        _cached_expiry[currency] = time.time() + BCV_TTL_SECONDS
        return rate
    except Exception as exc:
        print(f"[BCV] Error fetching {currency} rate: {exc}")
        return get_cached_bcv_rate(db, currency)


async def fetch_all_bcv_rates(db: Session) -> Dict[str, Optional[float]]:
    """Refresca todas las monedas del BCV. Secuencial: comparten la sesión de BD."""
    return {currency: await fetch_bcv_rate(db, currency) for currency in BCV_CURRENCIES}


def _reset_cache_for_test() -> None:
    """Solo para tests."""
    _cached_rates.clear()
    _cached_expiry.clear()
