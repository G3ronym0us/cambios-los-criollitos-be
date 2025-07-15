from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime

from app.database.connection import get_db
from app.schemas.currency_pair import (
    CurrencyPairCreate, CurrencyPairUpdate, CurrencyPairResponse, 
    CurrencyPairList, CurrencyPairStatusUpdate, CurrencyPairStats
)
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.currency_repository import CurrencyRepository
from app.core.dependencies import get_root_user, get_moderator_user
from app.models.user import User
from app.models.currency import CurrencyType

router = APIRouter(prefix="/currency-pairs", tags=["Currency Pairs"])

@router.post("/", response_model=CurrencyPairResponse, status_code=status.HTTP_201_CREATED)
async def create_currency_pair(
    pair_data: CurrencyPairCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Create new currency pair (ROOT access only)"""
    pair_repo = CurrencyPairRepository(db)
    currency_repo = CurrencyRepository(db)
    
    # Validate currencies exist
    from_currency = currency_repo.get_by_id(pair_data.from_currency_id)
    to_currency = currency_repo.get_by_id(pair_data.to_currency_id)
    
    if not from_currency:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"From currency with ID {pair_data.from_currency_id} not found"
        )
    
    if not to_currency:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"To currency with ID {pair_data.to_currency_id} not found"
        )
    
    # Check if pair already exists
    if pair_repo.pair_exists(pair_data.from_currency_id, pair_data.to_currency_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Currency pair {from_currency.symbol}-{to_currency.symbol} already exists"
        )
    
    # Validate Binance tracking requirements
    if pair_data.binance_tracked:
        # Check if one currency is FIAT and the other is CRYPTO
        valid_combination = (
            (from_currency.currency_type == CurrencyType.FIAT and to_currency.currency_type == CurrencyType.CRYPTO) or
            (from_currency.currency_type == CurrencyType.CRYPTO and to_currency.currency_type == CurrencyType.FIAT)
        )
        
        if not valid_combination:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Binance tracked pairs must be between FIAT and CRYPTO currencies"
            )
    
    try:
        pair = pair_repo.create_currency_pair(pair_data)
        return CurrencyPairResponse(**pair.dict())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/", response_model=CurrencyPairList)
async def get_currency_pairs(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    active_only: bool = Query(False),
    monitored_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get all currency pairs with pagination and filters (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    
    if monitored_only:
        pairs = pair_repo.get_monitored_pairs()
        total = len(pairs)
        pairs = pairs[skip:skip + limit]
    else:
        pairs = pair_repo.get_all_pairs(skip, limit, active_only)
        # For total count, we'd need another query or estimate
        total = len(pairs) + skip if len(pairs) == limit else skip + len(pairs)
    
    return CurrencyPairList(
        pairs=[CurrencyPairResponse(**pair.dict()) for pair in pairs],
        total=total,
        skip=skip,
        limit=limit
    )

@router.get("/monitored", response_model=List[CurrencyPairResponse])
async def get_monitored_pairs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get all monitored currency pairs for scraping (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    pairs = pair_repo.get_monitored_pairs()
    
    return [CurrencyPairResponse(**pair.dict()) for pair in pairs]

@router.get("/binance-tracked", response_model=List[CurrencyPairResponse])
async def get_binance_tracked_pairs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get all currency pairs tracked on Binance (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    pairs = pair_repo.get_binance_tracked_pairs()
    
    return [CurrencyPairResponse(**pair.dict()) for pair in pairs]

@router.get("/stats", response_model=CurrencyPairStats)
async def get_currency_pair_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Get currency pair statistics (ROOT access only)"""
    pair_repo = CurrencyPairRepository(db)
    all_pairs = pair_repo.get_all_pairs(limit=1000)
    
    stats = {
        "total_pairs": len(all_pairs),
        "active_pairs": len([p for p in all_pairs if p.is_active]),
        "monitored_pairs": len([p for p in all_pairs if p.is_monitored]),
        "pairs_by_currency": {}
    }
    
    # Count pairs by currency
    for pair in all_pairs:
        from_symbol = pair.from_currency.symbol if pair.from_currency else "Unknown"
        to_symbol = pair.to_currency.symbol if pair.to_currency else "Unknown"
        
        if from_symbol not in stats["pairs_by_currency"]:
            stats["pairs_by_currency"][from_symbol] = 0
        if to_symbol not in stats["pairs_by_currency"]:
            stats["pairs_by_currency"][to_symbol] = 0
            
        stats["pairs_by_currency"][from_symbol] += 1
        stats["pairs_by_currency"][to_symbol] += 1
    
    return CurrencyPairStats(**stats)

@router.get("/{pair_id}", response_model=CurrencyPairResponse)
async def get_currency_pair(
    pair_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get currency pair by ID (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    pair = pair_repo.get_by_id(pair_id)
    
    if not pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency pair not found"
        )
    
    return CurrencyPairResponse(**pair.dict())

@router.get("/symbol/{pair_symbol}", response_model=CurrencyPairResponse)
async def get_currency_pair_by_symbol(
    pair_symbol: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get currency pair by symbol (e.g., USDT-VES) (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    pair = pair_repo.get_by_symbol(pair_symbol)
    
    if not pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency pair '{pair_symbol}' not found"
        )
    
    return CurrencyPairResponse(**pair.dict())

@router.get("/currency/{currency_id}", response_model=List[CurrencyPairResponse])
async def get_pairs_by_currency(
    currency_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user)
):
    """Get all pairs that include a specific currency (MODERATOR+ access)"""
    pair_repo = CurrencyPairRepository(db)
    pairs = pair_repo.get_pairs_by_currency(currency_id)
    
    return [CurrencyPairResponse(**pair.dict()) for pair in pairs]

@router.put("/{pair_id}", response_model=CurrencyPairResponse)
async def update_currency_pair(
    pair_id: int,
    pair_data: CurrencyPairUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Update currency pair (ROOT access only)"""
    pair_repo = CurrencyPairRepository(db)
    
    # Check if pair exists
    existing_pair = pair_repo.get_by_id(pair_id)
    if not existing_pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency pair not found"
        )
    
    updated_pair = pair_repo.update_currency_pair(pair_id, pair_data)
    if not updated_pair:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update currency pair"
        )
    
    return CurrencyPairResponse(**updated_pair.dict())

@router.patch("/{pair_id}/status", response_model=CurrencyPairResponse)
async def update_pair_status(
    pair_id: int,
    status_data: CurrencyPairStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Update pair status (active/monitoring) (ROOT access only)"""
    pair_repo = CurrencyPairRepository(db)
    
    pair = pair_repo.get_by_id(pair_id)
    if not pair:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency pair not found"
        )
    
    # Update status fields
    if status_data.is_active is not None:
        pair_repo.toggle_active_status(pair_id, status_data.is_active)
    
    if status_data.is_monitored is not None:
        pair_repo.toggle_monitoring(pair_id, status_data.is_monitored)
    
    if status_data.binance_tracked is not None:
        pair_repo.toggle_binance_tracking(pair_id, status_data.binance_tracked)
    
    # Update banks_to_track and amount_to_track if provided
    if status_data.banks_to_track is not None or status_data.amount_to_track is not None:
        pair = pair_repo.get_by_id(pair_id)
        if status_data.banks_to_track is not None:
            pair.banks_to_track = status_data.banks_to_track
        if status_data.amount_to_track is not None:
            pair.amount_to_track = status_data.amount_to_track
        
        # Validate if binance_tracked is enabled
        if pair.binance_tracked:
            valid, error_msg = pair.validate_binance_tracking()
            if not valid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg
                )
        
        pair.updated_at = datetime.utcnow()
        pair_repo.db.commit()
    
    updated_pair = pair_repo.get_by_id(pair_id)
    return CurrencyPairResponse(**updated_pair.dict())

@router.delete("/{pair_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_currency_pair(
    pair_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Delete currency pair (ROOT access only)"""
    pair_repo = CurrencyPairRepository(db)
    
    if not pair_repo.get_by_id(pair_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency pair not found"
        )
    
    success = pair_repo.delete_currency_pair(pair_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete currency pair"
        )