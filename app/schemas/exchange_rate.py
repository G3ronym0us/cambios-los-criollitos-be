from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional
from uuid import UUID
from app.enums.pair_type import PairType

class ExchangeRateBase(BaseModel):
    currency_pair_uuid: UUID
    rate: float

class ExchangeRateCreate(ExchangeRateBase):
    percentage: Optional[float] = None
    inverse_percentage: bool = False

class ExchangeRateUpdate(BaseModel):
    currency_pair_uuid: Optional[UUID] = None
    rate: Optional[float] = None
    percentage: Optional[float] = None
    inverse_percentage: Optional[bool] = None
    is_active: Optional[bool] = None

class ExchangeRateResponse(BaseModel):
    uuid: UUID
    currency_pair_uuid: UUID
    pair_symbol: Optional[str] = None  # Incluido para facilidad (ej: "USDT/VES")
    pair_type: Optional[PairType] = None  # Tipo de par: base, derived, cross
    from_currency: str  # Incluido para compatibilidad
    to_currency: str    # Incluido para compatibilidad
    rate: float
    is_active: bool
    percentage: Optional[float] = None
    inverse_percentage: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    base_rate: Optional[float] = None
    manual_rate: Optional[float] = None
    is_manual: bool = False
    automatic_rate: Optional[float] = None

    @field_validator('is_manual', mode='before')
    @classmethod
    def validate_is_manual(cls, v):
        """Convert None to False for is_manual field"""
        return v if v is not None else False

    class Config:
        # Note: from_attributes removed to allow manual dict construction in enrich_rate_response()
        pass

class ManualRateRequest(BaseModel):
    currency_pair_uuid: UUID
    manual_rate: float

    class Config:
        json_schema_extra = {
            "example": {
                "currency_pair_uuid": "550e8400-e29b-41d4-a716-446655440000",
                "manual_rate": 45.5
            }
        }
