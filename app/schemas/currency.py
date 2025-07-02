from pydantic import BaseModel, validator
from typing import Optional
from datetime import datetime
from app.models.currency import CurrencyType

class CurrencyBase(BaseModel):
    name: str
    symbol: str
    description: Optional[str] = None
    currency_type: CurrencyType = CurrencyType.FIAT

    @validator('symbol')
    def validate_symbol(cls, v):
        if not v:
            raise ValueError('Symbol is required')
        if len(v) < 2 or len(v) > 10:
            raise ValueError('Symbol must be between 2 and 10 characters')
        if not v.replace('-', '').replace('_', '').isalnum():
            raise ValueError('Symbol can only contain letters, numbers, hyphens and underscores')
        return v.upper()

    @validator('name')
    def validate_name(cls, v):
        if not v or not v.strip():
            raise ValueError('Name is required')
        if len(v.strip()) < 2:
            raise ValueError('Name must be at least 2 characters long')
        return v.strip()

class CurrencyCreate(CurrencyBase):
    pass

class CurrencyUpdate(BaseModel):
    name: Optional[str] = None
    symbol: Optional[str] = None
    description: Optional[str] = None
    currency_type: Optional[CurrencyType] = None

    @validator('symbol')
    def validate_symbol(cls, v):
        if v is not None:
            if len(v) < 2 or len(v) > 10:
                raise ValueError('Symbol must be between 2 and 10 characters')
            if not v.replace('-', '').replace('_', '').isalnum():
                raise ValueError('Symbol can only contain letters, numbers, hyphens and underscores')
            return v.upper()
        return v

    @validator('name')
    def validate_name(cls, v):
        if v is not None:
            if not v.strip():
                raise ValueError('Name cannot be empty')
            if len(v.strip()) < 2:
                raise ValueError('Name must be at least 2 characters long')
            return v.strip()
        return v

class CurrencyResponse(CurrencyBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class CurrencyList(BaseModel):
    currencies: list[CurrencyResponse]
    total: int
    skip: int
    limit: int