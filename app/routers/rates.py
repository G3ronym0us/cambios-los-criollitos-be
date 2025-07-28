from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database.connection import get_db
from app.schemas.exchange_rate import ExchangeRateResponse, ManualRateRequest
from app.repositories.exchange_rate_repository import ExchangeRateRepository
from app.core.dependencies import get_root_user, get_moderator_user

router = APIRouter(prefix="/rates", tags=["Exchange Rates"])

@router.get("/latest/{from_currency}/{to_currency}", response_model=List[ExchangeRateResponse])
async def get_latest_rates_for_pair(
    from_currency: str,
    to_currency: str,
    limit: int = Query(10, ge=1, le=100, description="Number of latest rates to return"),
    db: Session = Depends(get_db),
    current_user = Depends(get_root_user)
):
    """Get the latest exchange rates for a specific currency pair (USER+ access)"""
    repo = ExchangeRateRepository(db)
    
    # Convert to uppercase for consistency
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    
    # Get latest rates for the pair
    rates = repo.get_latest_rates_for_pair(from_currency, to_currency, limit)
    
    if not rates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exchange rates found for pair {from_currency}-{to_currency}"
        )
    
    return [ExchangeRateResponse.model_validate(rate) for rate in rates]

@router.get("/pair/{pair_symbol}/latest", response_model=List[ExchangeRateResponse])
async def get_latest_rates_by_symbol(
    pair_symbol: str,
    limit: int = Query(10, ge=1, le=100, description="Number of latest rates to return"),
    db: Session = Depends(get_db),
    current_user = Depends(get_root_user)
):
    """Get the latest exchange rates for a currency pair using pair symbol (e.g., USDT-VES) (USER+ access)"""
    
    # Parse pair symbol (format: FROM-TO)
    try:
        from_currency, to_currency = pair_symbol.upper().split('-')
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pair symbol format. Use format: FROM-TO (e.g., USDT-VES)"
        )
    
    repo = ExchangeRateRepository(db)
    
    # Get latest rates for the pair
    rates = repo.get_latest_rates_for_pair(from_currency, to_currency, limit)
    
    if not rates:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exchange rates found for pair {pair_symbol}"
        )
    
    return [ExchangeRateResponse.model_validate(rate) for rate in rates]

@router.post("/manual/{from_currency}/{to_currency}", response_model=ExchangeRateResponse)
async def set_manual_rate(
    from_currency: str,
    to_currency: str,
    request: ManualRateRequest,
    db: Session = Depends(get_db),
    current_user = Depends(get_moderator_user)
):
    """Set manual rate for a currency pair (MODERATOR+ access)"""
    repo = ExchangeRateRepository(db)
    
    # Convert to uppercase for consistency
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    
    # Set manual rate
    rate = repo.set_manual_rate(from_currency, to_currency, request.manual_rate)
    
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No exchange rate found for pair {from_currency}-{to_currency}"
        )
    
    return ExchangeRateResponse.model_validate(rate)

@router.delete("/manual/{from_currency}/{to_currency}", response_model=ExchangeRateResponse)
async def remove_manual_rate(
    from_currency: str,
    to_currency: str,
    db: Session = Depends(get_db),
    current_user = Depends(get_moderator_user)
):
    """Remove manual rate for a currency pair (MODERATOR+ access)"""
    repo = ExchangeRateRepository(db)
    
    # Convert to uppercase for consistency
    from_currency = from_currency.upper()
    to_currency = to_currency.upper()
    
    # Remove manual rate
    rate = repo.remove_manual_rate(from_currency, to_currency)
    
    if not rate:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No manual rate found for pair {from_currency}-{to_currency}"
        )
    
    return ExchangeRateResponse.model_validate(rate)