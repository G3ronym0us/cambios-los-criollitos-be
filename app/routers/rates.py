from fastapi import APIRouter, Depends, HTTPException, status, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, Field

from app.database.connection import get_db
from app.schemas.exchange_rate import ExchangeRateResponse, ExchangeRateCreate, ExchangeRateUpdate, ManualRateRequest
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.core.dependencies import get_root_user, get_moderator_user, get_current_user
from app.core.external_auth import verify_external_rate_key
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
        "automatic_rate": rate.automatic_rate,
        "rounding_mode": rate.currency_pair.rounding_mode if rate.currency_pair else None,
        "rounding_step": float(rate.currency_pair.rounding_step) if rate.currency_pair and rate.currency_pair.rounding_step is not None else None,
        "rounding_direction": rate.currency_pair.rounding_direction if rate.currency_pair else None,
        "rounding_amount_side": rate.currency_pair.rounding_amount_side if rate.currency_pair else None,
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


# ===== ACTUALIZACIÓN EXTERNA DE TASAS (script del operador, auth por X-API-Key) =====
#
# Pensado para monedas cuyo P2P NO se puede leer desde el servidor. Caso actual: BRL.
# Binance dejó de exponer anuncios USDT/BRL en su API pública global, así que el
# servidor (fuera de Brasil) recibe 0 anuncios. La solución: un script corre en la
# máquina del operador (que SÍ está en Brasil y ve los anuncios reales), calcula la
# tasa replicando la config del sistema, y la empuja aquí con X-API-Key.
#
# PARA HABILITAR OTRA MONEDA EN EL FUTURO:
#   1. Agrega su pair_symbol a EXTERNAL_UPDATABLE_PAIRS (abajo).
#   2. Asegúrate de que el par exista en la DB con su config de banks_to_track /
#      amount_to_track (el script la lee vía GET /rates/external/config/{pair}).
#   3. Configura ese par en el script del operador (variable PAIR).

EXTERNAL_UPDATABLE_PAIRS = {"BRL-USDT", "USDT-BRL"}
EXTERNAL_RATE_MAX = 1_000_000.0


class ExternalRateUpdate(BaseModel):
    """Payload del script externo del operador."""
    pair_symbol: str = Field(..., examples=["BRL-USDT"])
    rate: float = Field(..., gt=0, examples=[5.45])


@router.get("/external/config/{pair_symbol}")
async def get_external_pair_config(
    pair_symbol: str,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_external_rate_key),
):
    """
    Config de rastreo Binance del par, para que el script externo replique EXACTAMENTE
    al sistema (mismos métodos de pago, monto y dirección). Protegido por X-API-Key.

    Devuelve fiat / asset / trade_type / pay_types / amount tal como están configurados
    en el panel. Si mañana cambias los bancos o el monto desde el admin, el script los
    toma solo sin tocar nada.
    """
    from app.repositories.currency_pair_repository import CurrencyPairRepository

    pair_repo = CurrencyPairRepository(db)
    pair = pair_repo.get_by_symbol(pair_symbol)
    if not pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Par {pair_symbol} no encontrado",
        )

    # Mismo criterio que BinanceP2PScraper: el FIAT define dirección y trade_type
    if pair.from_currency.currency_type.name == "FIAT":
        fiat, asset, trade_type = pair.from_currency.symbol, pair.to_currency.symbol, "BUY"
    elif pair.to_currency.currency_type.name == "FIAT":
        fiat, asset, trade_type = pair.to_currency.symbol, pair.from_currency.symbol, "SELL"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Par {pair_symbol} no es FIAT/CRYPTO",
        )

    return {
        "pair_symbol": pair.pair_symbol,
        "fiat": fiat,
        "asset": asset,
        "trade_type": trade_type,
        "pay_types": pair.banks_to_track or [],
        "amount": float(pair.amount_to_track) if pair.amount_to_track else None,
    }


@router.post("/external", response_model=ExchangeRateResponse)
async def set_external_rate(
    request: ExternalRateUpdate,
    db: Session = Depends(get_db),
    _: bool = Depends(verify_external_rate_key),
):
    """
    Actualizar una tasa desde el script externo del operador (auth por X-API-Key).

    - Solo acepta pares de EXTERNAL_UPDATABLE_PAIRS (lista blanca).
    - Valida rango para que un error de scraping no envenene la cotización.
    - Reutiliza la lógica de tasa manual y dispara el scraper para recalcular las
      tasas derivadas y cruzadas que dependan de este par base.
    """
    pair_symbol = request.pair_symbol.upper()

    if pair_symbol not in EXTERNAL_UPDATABLE_PAIRS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Par {pair_symbol} no habilitado para actualización externa",
        )

    if not (0 < request.rate < EXTERNAL_RATE_MAX):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Tasa fuera de rango válido (0 < rate < {EXTERNAL_RATE_MAX})",
        )

    from app.repositories.currency_pair_repository import CurrencyPairRepository

    pair_repo = CurrencyPairRepository(db)
    currency_pair = pair_repo.get_by_symbol(pair_symbol)
    if not currency_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Par {pair_symbol} no encontrado",
        )

    repo = ExchangeRateRepository(db)
    rate = repo.set_manual_rate(
        currency_pair.from_currency.symbol,
        currency_pair.to_currency.symbol,
        request.rate,
    )
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"No se pudo actualizar la tasa de {pair_symbol}",
        )

    db.refresh(rate)

    # Recalcular derivadas/cruzadas en background
    try:
        task = manual_scrape.delay()
        print(f"🔄 Tasa externa {pair_symbol}={request.rate} aplicada. Scraper disparado. Task ID: {task.id}")
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
