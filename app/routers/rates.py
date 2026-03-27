from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from uuid import UUID

from app.database.connection import get_db
from app.schemas.exchange_rate import ExchangeRateResponse, ExchangeRateCreate, ExchangeRateUpdate, ManualRateRequest
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.core.dependencies import get_root_user, get_moderator_user, get_current_user
from app.models.user import User
from app.tasks.scraping_tasks import manual_scrape

router = APIRouter(prefix="/rates", tags=["Exchange Rates"])


# ===== Helper Function =====

def enrich_rate_response(rate) -> dict:
    """Enriquecer respuesta de rate con currency_pair_uuid, pair_symbol y pair_type"""
    result = {
        "uuid": rate.uuid,
        "currency_pair_uuid": rate.currency_pair.uuid if rate.currency_pair else None,
        "pair_symbol": rate.currency_pair.pair_symbol if rate.currency_pair else f"{rate.from_currency}/{rate.to_currency}",
        "pair_type": rate.currency_pair.pair_type if rate.currency_pair else None,
        "from_currency": rate.from_currency,
        "to_currency": rate.to_currency,
        "rate": rate.rate,
        "base_rate": rate.base_rate,
        "is_active": rate.is_active,
        "percentage": rate.percentage,
        "inverse_percentage": rate.inverse_percentage,
        "created_at": rate.created_at,
        "updated_at": rate.updated_at,
        "manual_rate": rate.manual_rate,
        "is_manual": rate.is_manual,
        "automatic_rate": rate.automatic_rate
    }
    print(f"🔍 DEBUG enrich_rate_response: pair_type={result['pair_type']}, currency_pair_uuid={result['currency_pair_uuid']}")
    return result


# ===== ENDPOINTS CON currency_pair_uuid =====

@router.get("/latest/{currency_pair_uuid}", response_model=List[ExchangeRateResponse])
async def get_latest_rates_for_pair(
    currency_pair_uuid: UUID,
    limit: int = Query(10, ge=1, le=100, description="Number of latest rates to return"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Get the latest exchange rates for a specific currency pair (USER+ access)

    Returns historical rates for the specified currency pair, ordered by most recent first.
    """
    repo = ExchangeRateRepository(db)
    from app.repositories.currency_pair_repository import CurrencyPairRepository

    # Obtener el currency_pair
    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_uuid(currency_pair_uuid)

    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {currency_pair_uuid} not found"
        )

    # Get latest rates for the pair
    rates = repo.get_latest_rates_for_pair(
        currency_pair.from_currency.symbol,
        currency_pair.to_currency.symbol,
        limit
    )

    if not rates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exchange rates found for pair {currency_pair.pair_symbol}"
        )

    return [ExchangeRateResponse(**enrich_rate_response(rate)) for rate in rates]

@router.post("/manual", response_model=ExchangeRateResponse)
async def set_manual_rate(
    request: ManualRateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_moderator_user)
):
    """
    Set manual rate for a currency pair (MODERATOR+ access)

    **Request Body**:
    ```json
    {
      "currency_pair_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "manual_rate": 50.5
    }
    ```

    Después de establecer la tasa manual, se ejecuta automáticamente el scraper
    para actualizar todas las tasas derivadas y cross rates que dependan de esta tasa base.
    """
    repo = ExchangeRateRepository(db)
    from app.repositories.currency_pair_repository import CurrencyPairRepository

    # Obtener el currency_pair
    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_uuid(request.currency_pair_uuid)

    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {request.currency_pair_uuid} not found"
        )

    # Set manual rate usando los símbolos del par
    rate = repo.set_manual_rate(
        currency_pair.from_currency.symbol,
        currency_pair.to_currency.symbol,
        request.manual_rate
    )

    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exchange rate found for pair {currency_pair.pair_symbol}"
        )

    # Refrescar para obtener las relaciones
    db.refresh(rate)

    # Ejecutar scraper en background para actualizar tasas derivadas
    try:
        task = manual_scrape.delay()
        print(f"🔄 Scraper ejecutado después de actualizar tasa manual {currency_pair.pair_symbol}. Task ID: {task.id}")
    except Exception as e:
        print(f"⚠️ No se pudo ejecutar el scraper en background: {e}")

    return ExchangeRateResponse(**enrich_rate_response(rate))

@router.put("/manual/{currency_pair_uuid}/disable", response_model=ExchangeRateResponse)
async def disable_manual_rate(
    currency_pair_uuid: UUID,
    db: Session = Depends(get_db),
    current_user = Depends(get_moderator_user)
):
    """
    Desactivar modo manual y volver a tasas automáticas (MODERATOR+ access)

    Este endpoint desactiva el modo manual para un par de monedas, permitiendo que
    el scraper vuelva a actualizar automáticamente las tasas para este par.

    **Flujo:**
    1. Desactiva el flag `is_manual` en la tasa actual
    2. Vuelve a usar la `automatic_rate` si existe
    3. Ejecuta el scraper para obtener la tasa más reciente desde Binance

    Después de ejecutar este endpoint, el par volverá a actualizarse automáticamente
    en cada ejecución del scraper.
    """
    repo = ExchangeRateRepository(db)
    from app.repositories.currency_pair_repository import CurrencyPairRepository

    # Obtener el currency_pair
    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_uuid(currency_pair_uuid)

    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {currency_pair_uuid} not found"
        )

    # Remove manual rate usando los símbolos del par
    rate = repo.remove_manual_rate(
        currency_pair.from_currency.symbol,
        currency_pair.to_currency.symbol
    )

    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No manual rate found for pair {currency_pair.pair_symbol}"
        )

    # Refrescar para obtener las relaciones
    db.refresh(rate)

    # Ejecutar scraper en background para obtener tasa actualizada automáticamente
    try:
        task = manual_scrape.delay()
        print(f"🔄 Modo manual desactivado para {currency_pair.pair_symbol}. Scraper ejecutado. Task ID: {task.id}")
    except Exception as e:
        print(f"⚠️ No se pudo ejecutar el scraper en background: {e}")

    return ExchangeRateResponse(**enrich_rate_response(rate))

# ===== NUEVOS ENDPOINTS (Recomendados) =====

@router.post("", response_model=ExchangeRateResponse, status_code=status.HTTP_201_CREATED)
async def create_or_update_rate(
    rate_data: ExchangeRateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Crear o actualizar tasa de cambio para un par de divisas.
    Solo puede haber un registro activo por par.

    **Requiere**: MODERATOR o superior

    **Ejemplo**:
    ```json
    {
      "currency_pair_uuid": "550e8400-e29b-41d4-a716-446655440000",
      "rate": 36.5,
      "source": "binance_p2p",
      "percentage": 5.0,
      "inverse_percentage": false
    }
    ```

    Después de crear/actualizar la tasa, se ejecuta automáticamente el scraper
    para actualizar todas las tasas derivadas y cross rates que dependan de esta tasa base.
    """
    repo = ExchangeRateRepository(db)

    try:
        rate = repo.create_or_update_rate(rate_data)

        # Ejecutar scraper en background para actualizar tasas derivadas
        try:
            task = manual_scrape.delay()
            print(f"🔄 Scraper ejecutado después de crear/actualizar tasa. Task ID: {task.id}")
        except Exception as e:
            print(f"⚠️ No se pudo ejecutar el scraper en background: {e}")

        return ExchangeRateResponse(**enrich_rate_response(rate))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.get("", response_model=List[ExchangeRateResponse])
async def get_all_active_rates(
    db: Session = Depends(get_db)
):
    """
    Obtener todas las tasas de cambio activas.
    Solo hay un registro activo por currency_pair.

    **Acceso**: Público (sin autenticación requerida)
    """
    repo = ExchangeRateRepository(db)
    rates = repo.get_all_active_rates()

    # Construir respuestas con datos enriquecidos
    enriched_rates = [enrich_rate_response(rate) for rate in rates]
    return enriched_rates


@router.get("/historical/{currency_pair_uuid}", response_model=ExchangeRateResponse)
async def get_rate_at_datetime(
    currency_pair_uuid: UUID,
    at: datetime = Query(..., description="Fecha y hora (ISO 8601, ej: 2026-03-21T14:30:00Z)"),
    db: Session = Depends(get_db),
    current_user = Depends(get_current_user)
):
    """
    Obtener la tasa que estaba activa para un par en un momento específico.

    Devuelve el registro con `created_at` más reciente que sea <= `at`.
    """
    from app.repositories.currency_pair_repository import CurrencyPairRepository
    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_uuid(currency_pair_uuid)
    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair with UUID {currency_pair_uuid} not found"
        )

    repo = ExchangeRateRepository(db)
    rate = repo.get_rate_at_datetime(currency_pair.id, at)

    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No rate found for pair {currency_pair.pair_symbol} at or before {at.isoformat()}"
        )

    return ExchangeRateResponse(**enrich_rate_response(rate))


@router.get("/by-pair/{currency_pair_uuid}", response_model=ExchangeRateResponse)
async def get_active_rate_by_pair(
    currency_pair_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Obtener la tasa activa para un par de divisas específico.

    **Acceso**: USER o superior
    """
    repo = ExchangeRateRepository(db)
    rate = repo.get_active_rate_by_pair(currency_pair_uuid)

    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No active rate found for currency pair {currency_pair_uuid}"
        )

    return ExchangeRateResponse(**enrich_rate_response(rate))


@router.get("/{rate_uuid}", response_model=ExchangeRateResponse)
async def get_rate_by_uuid(
    rate_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Obtener tasa de cambio por UUID"""
    repo = ExchangeRateRepository(db)
    rate = repo.get_by_uuid(rate_uuid)

    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exchange rate with UUID {rate_uuid} not found"
        )

    return ExchangeRateResponse(**enrich_rate_response(rate))


@router.put("/{rate_uuid}", response_model=ExchangeRateResponse)
async def update_rate(
    rate_uuid: UUID,
    update_data: ExchangeRateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Actualizar tasa de cambio existente.

    **Requiere**: MODERATOR o superior

    Después de actualizar la tasa, se ejecuta automáticamente el scraper
    para actualizar todas las tasas derivadas y cross rates que dependan de esta tasa base.
    """
    repo = ExchangeRateRepository(db)

    try:
        rate = repo.update_rate(rate_uuid, update_data)
        if not rate:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Exchange rate with UUID {rate_uuid} not found"
            )

        # Ejecutar scraper en background para actualizar tasas derivadas
        try:
            task = manual_scrape.delay()
            print(f"🔄 Scraper ejecutado después de actualizar tasa {rate_uuid}. Task ID: {task.id}")
        except Exception as e:
            print(f"⚠️ No se pudo ejecutar el scraper en background: {e}")

        return ExchangeRateResponse(**enrich_rate_response(rate))
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )


@router.delete("/{rate_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_rate(
    rate_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Eliminar (desactivar) tasa de cambio.

    **Requiere**: MODERATOR o superior
    """
    repo = ExchangeRateRepository(db)

    success = repo.delete_rate(rate_uuid)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Exchange rate with UUID {rate_uuid} not found"
        )
