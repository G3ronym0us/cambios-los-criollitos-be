from pydantic import BaseModel, validator
from datetime import datetime
from typing import Optional

class TransactionBase(BaseModel):
    from_currency: str
    to_currency: str
    from_amount: float
    to_amount: float
    exchange_rate: float
    transaction_type: str = "conversion"
    
    @validator('from_currency', 'to_currency')
    def validate_currency(cls, v):
        if len(v) != 3:
            raise ValueError('Currency code must be exactly 3 characters')
        return v.upper()
    
    @validator('from_amount', 'to_amount')
    def validate_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v
    
    @validator('exchange_rate')
    def validate_exchange_rate(cls, v):
        if v <= 0:
            raise ValueError('Exchange rate must be greater than 0')
        return v

class TransactionCreate(TransactionBase):
    user_id: Optional[int] = None

class TransactionUpdate(BaseModel):
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None
    from_amount: Optional[float] = None
    to_amount: Optional[float] = None
    exchange_rate: Optional[float] = None
    transaction_type: Optional[str] = None
    
    @validator('from_currency', 'to_currency')
    def validate_currency(cls, v):
        if v is not None:
            if len(v) != 3:
                raise ValueError('Currency code must be exactly 3 characters')
            return v.upper()
        return v
    
    @validator('from_amount', 'to_amount')
    def validate_amount(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v
    
    @validator('exchange_rate')
    def validate_exchange_rate(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Exchange rate must be greater than 0')
        return v

class TransactionResponse(TransactionBase):
    id: int
    user_id: Optional[int]
    created_at: datetime
    
    class Config:
        from_attributes = True

class TransactionList(BaseModel):
    transactions: list[TransactionResponse]
    total: int
    page: int
    per_page: int
    total_pages: int