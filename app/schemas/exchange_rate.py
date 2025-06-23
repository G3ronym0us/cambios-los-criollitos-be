from pydantic import BaseModel
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
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
