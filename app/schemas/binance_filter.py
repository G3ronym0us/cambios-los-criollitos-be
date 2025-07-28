from pydantic import BaseModel, validator
from typing import List, Optional

class TradeMethodResponse(BaseModel):
    identifier: str
    icon_url: str
    name: Optional[str] = None
    short_name: Optional[str] = None
    bg_color: Optional[str] = None
    
    class Config:
        from_attributes = True

class TradeMethodSimple(BaseModel):
    identifier: str
    icon_url: str
    
    class Config:
        from_attributes = True

class BinanceFilterResponse(BaseModel):
    fiat_currency: str
    trade_methods: List[TradeMethodResponse]
    
    class Config:
        from_attributes = True

class BinanceFilterRequest(BaseModel):
    fiat_currency: str
    
    @validator('fiat_currency')
    def validate_fiat_currency(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('fiat_currency is required')
        return v.upper().strip()
    
    class Config:
        from_attributes = True

class BinanceFilterSimpleResponse(BaseModel):
    fiat_currency: str
    trade_methods: List[TradeMethodSimple]
    
    class Config:
        from_attributes = True