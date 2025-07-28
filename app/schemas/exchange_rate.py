from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional

class ExchangeRateBase(BaseModel):
    from_currency: str
    to_currency: str
    rate: float
    source: str = "manual"

class ExchangeRateCreate(ExchangeRateBase):
    pass

class ExchangeRateResponse(ExchangeRateBase):
    id: int
    is_active: bool
    percentage: Optional[float] = None
    inverse_percentage: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None
    manual_rate: Optional[float] = None
    is_manual: bool = False
    automatic_rate: Optional[float] = None

    @field_validator('is_manual', mode='before')
    @classmethod
    def validate_is_manual(cls, v):
        """Convert None to False for is_manual field"""
        return v if v is not None else False

    class Config:
        from_attributes = True

class ManualRateRequest(BaseModel):
    manual_rate: float

    class Config:
        json_schema_extra = {
            "example": {
                "manual_rate": 45.5
            }
        }
