from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database.connection import get_db
from app.schemas.binance_filter import (
    BinanceFilterRequest, BinanceFilterResponse, 
    BinanceFilterSimpleResponse, TradeMethodSimple
)
from app.services.binance_filter_service import BinanceFilterService
from app.core.dependencies import get_moderator_user
from app.models.user import User

router = APIRouter(prefix="/binance", tags=["Binance P2P"])

@router.post("/filter-conditions", response_model=BinanceFilterResponse)
async def get_binance_filter_conditions(
    request: BinanceFilterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Get Binance P2P filter conditions for a specific fiat currency
    Returns available trade methods with complete information
    (MODERATOR+ access required)
    """
    try:
        result = await BinanceFilterService.get_filter_conditions(request.fiat_currency)
        
        if not result:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not fetch filter conditions for {request.fiat_currency} from Binance P2P API"
            )
        
        return BinanceFilterResponse(**result)
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching Binance filter conditions: {str(e)}"
        )

@router.post("/trade-methods", response_model=BinanceFilterSimpleResponse)
async def get_binance_trade_methods(
    request: BinanceFilterRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Get simplified Binance P2P trade methods for a specific fiat currency
    Returns only identifier and icon URL for each payment method
    (MODERATOR+ access required)
    """
    try:
        trade_methods = await BinanceFilterService.get_trade_methods_only(request.fiat_currency)
        
        if not trade_methods:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not fetch trade methods for {request.fiat_currency} from Binance P2P API"
            )
        
        return BinanceFilterSimpleResponse(
            fiat_currency=request.fiat_currency.upper(),
            trade_methods=[TradeMethodSimple(**method) for method in trade_methods]
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching Binance trade methods: {str(e)}"
        )

@router.get("/trade-methods/{fiat_currency}", response_model=List[TradeMethodSimple])
async def get_trade_methods_by_currency(
    fiat_currency: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """
    Get Binance P2P trade methods for a specific fiat currency (GET endpoint)
    Returns simplified list of payment methods
    (MODERATOR+ access required)
    """
    try:
        trade_methods = await BinanceFilterService.get_trade_methods_only(fiat_currency)
        
        if not trade_methods:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Could not fetch trade methods for {fiat_currency} from Binance P2P API"
            )
        
        return [TradeMethodSimple(**method) for method in trade_methods]
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error fetching Binance trade methods: {str(e)}"
        )