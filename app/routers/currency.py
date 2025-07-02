from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database.connection import get_db
from app.schemas.currency import (
    CurrencyCreate, CurrencyUpdate, CurrencyResponse, CurrencyList
)
from app.repositories.currency_repository import CurrencyRepository
from app.core.dependencies import get_root_user
from app.models.user import User
from app.models.currency import CurrencyType

router = APIRouter(prefix="/currencies", tags=["Currencies"])

@router.post("/", response_model=CurrencyResponse, status_code=status.HTTP_201_CREATED)
async def create_currency(
    currency_data: CurrencyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Create new currency (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    
    # Check if symbol already exists
    if currency_repo.symbol_exists(currency_data.symbol):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Currency with symbol '{currency_data.symbol}' already exists"
        )
    
    currency = currency_repo.create_currency(currency_data)
    return CurrencyResponse(**currency.dict())

@router.get("/", response_model=CurrencyList)
async def get_currencies(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    currency_type: Optional[CurrencyType] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Get all currencies with pagination and filters (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    
    if search:
        currencies = currency_repo.search_currencies(search)
        total = len(currencies)
        currencies = currencies[skip:skip + limit]
    elif currency_type:
        currencies = currency_repo.get_by_type(currency_type)
        total = len(currencies)
        currencies = currencies[skip:skip + limit]
    else:
        currencies = currency_repo.get_all_currencies(skip, limit)
        # For total count, we'd need another query or estimate
        total = len(currencies) + skip if len(currencies) == limit else skip + len(currencies)
    
    return CurrencyList(
        currencies=[CurrencyResponse(**currency.dict()) for currency in currencies],
        total=total,
        skip=skip,
        limit=limit
    )

@router.get("/{currency_id}", response_model=CurrencyResponse)
async def get_currency(
    currency_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Get currency by ID (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    currency = currency_repo.get_by_id(currency_id)
    
    if not currency:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency not found"
        )
    
    return CurrencyResponse(**currency.dict())

@router.get("/symbol/{symbol}", response_model=CurrencyResponse)
async def get_currency_by_symbol(
    symbol: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Get currency by symbol (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    currency = currency_repo.get_by_symbol(symbol)
    
    if not currency:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Currency with symbol '{symbol}' not found"
        )
    
    return CurrencyResponse(**currency.dict())

@router.put("/{currency_id}", response_model=CurrencyResponse)
async def update_currency(
    currency_id: int,
    currency_data: CurrencyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Update currency (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    
    # Check if currency exists
    existing_currency = currency_repo.get_by_id(currency_id)
    if not existing_currency:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency not found"
        )
    
    # Check if new symbol conflicts with existing currencies
    if currency_data.symbol and currency_repo.symbol_exists(currency_data.symbol, exclude_id=currency_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Currency with symbol '{currency_data.symbol}' already exists"
        )
    
    updated_currency = currency_repo.update_currency(currency_id, currency_data)
    if not updated_currency:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update currency"
        )
    
    return CurrencyResponse(**updated_currency.dict())

@router.delete("/{currency_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_currency(
    currency_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_root_user)
):
    """Delete currency (ROOT access only)"""
    currency_repo = CurrencyRepository(db)
    
    if not currency_repo.get_by_id(currency_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Currency not found"
        )
    
    success = currency_repo.delete_currency(currency_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete currency"
        )

@router.get("/types/available", response_model=List[str])
async def get_available_currency_types(
    current_user: User = Depends(get_root_user)
):
    """Get available currency types (ROOT access only)"""
    return [currency_type.value for currency_type in CurrencyType]