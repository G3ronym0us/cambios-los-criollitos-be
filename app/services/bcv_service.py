"""
Servicio para la tasa oficial del BCV (USD/VES).

Port de la lógica en whatsapp-bot/src/rates.ts (fetchBcvRate). La fuente
externa es ve.dolarapi.com. El backend cachea en memoria con TTL 5min y
persiste cada fetch exitoso en `bcv_rates` para auditoría/histórico.
"""

import time
from typing import Optional
import httpx
from sqlalchemy.orm import Session

from app.models.bcv_rate import BcvRate


BCV_URL = "https://ve.dolarapi.com/v1/dolares/oficial"
BCV_SOURCE = "ve.dolarapi.com"
BCV_TTL_SECONDS = 5 * 60
BCV_FETCH_TIMEOUT = 10.0


# Cache en memoria del proceso (igual que el bot)
_cached_rate: Optional[float] = None
_cached_expiry: float = 0.0


def get_cached_bcv_rate(db: Session) -> Optional[float]:
    """
    Retorna la tasa BCV cacheada. Si la cache en memoria expiró, consulta
    el último registro en la tabla `bcv_rates`. No hace fetch HTTP (eso lo
    hace la Celery task `refresh_bcv_rate`).
    """
    global _cached_rate, _cached_expiry
    now = time.time()
    if _cached_rate is not None and now < _cached_expiry:
        return _cached_rate

    latest = (
        db.query(BcvRate)
        .order_by(BcvRate.fetched_at.desc())
        .first()
    )
    if latest is None:
        return None
    _cached_rate = float(latest.rate)
    _cached_expiry = now + BCV_TTL_SECONDS
    return _cached_rate


async def fetch_bcv_rate(db: Session) -> Optional[float]:
    """
    Hace fetch HTTP a la API del BCV y persiste el resultado.
    Si falla la red, devuelve la última cacheada (o None si no hay).
    """
    global _cached_rate, _cached_expiry
    try:
        async with httpx.AsyncClient(timeout=BCV_FETCH_TIMEOUT) as client:
            response = await client.get(BCV_URL)
            response.raise_for_status()
            data = response.json()
        promedio = data.get("promedio")
        if not isinstance(promedio, (int, float)) or promedio <= 0:
            return get_cached_bcv_rate(db)

        rate = float(promedio)
        record = BcvRate(rate=rate, source=BCV_SOURCE)
        db.add(record)
        db.commit()

        _cached_rate = rate
        _cached_expiry = time.time() + BCV_TTL_SECONDS
        return rate
    except Exception as exc:
        print(f"[BCV] Error fetching rate: {exc}")
        return get_cached_bcv_rate(db)


def _reset_cache_for_test() -> None:
    """Solo para tests."""
    global _cached_rate, _cached_expiry
    _cached_rate = None
    _cached_expiry = 0.0
