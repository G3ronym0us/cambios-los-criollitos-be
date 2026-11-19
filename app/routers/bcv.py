from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.models.bcv_rate import BcvRate
from app.models.user import User
from app.schemas.bcv import BcvCurrency, BcvRateResponse
from app.services import valuation
from app.services.bcv_service import fetch_bcv_rate, latest_bcv_rate

router = APIRouter(prefix="/bcv", tags=["BCV"])


def _to_response(row: BcvRate) -> BcvRateResponse:
    return BcvRateResponse(
        currency=row.currency,
        rate=float(row.rate),
        source=row.source,
        fetched_at=row.fetched_at,
    )


@router.get("", response_model=BcvRateResponse)
async def get_bcv(
    currency: BcvCurrency = Query("USD", description="Moneda oficial del BCV"),
    db: Session = Depends(get_db),
):
    """
    Tasa oficial del BCV que rige ahora.

    **Acceso**: Público. Si todavía no hay ningún registro (primer arranque
    sin worker de Celery), se hace un fetch puntual para no dejar la
    calculadora sin el equivalente BCV.
    """
    row = latest_bcv_rate(db, currency)
    if row is None:
        if await fetch_bcv_rate(db, currency) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No hay una tasa BCV {currency} disponible",
            )
        row = latest_bcv_rate(db, currency)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay una tasa BCV {currency} disponible",
        )
    return _to_response(row)


@router.get("/historical", response_model=BcvRateResponse)
async def get_bcv_at(
    at: datetime = Query(..., description="Fecha y hora (ISO 8601, ej: 2026-03-21T14:30:00Z)"),
    currency: BcvCurrency = Query("USD", description="Moneda oficial del BCV"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Tasa oficial BCV que REGÍA en el momento indicado.

    Cotizar con el BCV de hoy un monto de ayer cambia el trato, así que se
    devuelve el último registro anterior a `at` y no el vigente.

    **Acceso**: autenticado (igual que el histórico de un par).
    """
    row = valuation.historical_bcv_row(db, at, currency)
    if row is None or row.rate <= 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No hay una tasa BCV {currency} registrada hasta {at.isoformat()}",
        )
    return _to_response(row)
