from pydantic import BaseModel, validator, Field
from typing import Optional, List, Literal
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from app.schemas.currency import CurrencyResponse
from app.enums.pair_type import PairType

class CurrencyPairBase(BaseModel):
    from_currency_uuid: UUID
    to_currency_uuid: UUID
    pair_type: PairType = PairType.BASE
    base_pair_uuid: Optional[UUID] = None
    derived_percentage: Optional[Decimal] = None
    use_inverse_percentage: bool = False
    description: Optional[str] = None
    is_active: bool = True
    is_monitored: bool = True
    binance_tracked: bool = False
    banks_to_track: Optional[List[str]] = None
    amount_to_track: Optional[Decimal] = None
    usdt_reference_side: Optional[Literal["FROM", "TO"]] = Field(None, description="Which side is the USDT reference (FROM or TO currency)")
    usdt_manual_rate: Optional[float] = Field(None, description="Manual USDT rate: reference_amount * rate = amount_usdt")
    usdt_pair_uuid: Optional[UUID] = Field(None, description="UUID of CurrencyPair used for auto USDT rate")
    usdt_pair_inverse: bool = Field(False, description="If True, use 1/rate from the conversion pair")

    @validator('to_currency_uuid')
    def validate_different_currencies(cls, v, values):
        if 'from_currency_uuid' in values and v == values['from_currency_uuid']:
            raise ValueError('From and to currencies must be different')
        return v

    @validator('banks_to_track')
    def validate_banks_to_track(cls, v, values):
        if values.get('binance_tracked', False):
            if not v or len(v) == 0:
                raise ValueError('banks_to_track is required when binance_tracked is True')
        return v

    @validator('amount_to_track')
    def validate_amount_to_track(cls, v, values):
        if values.get('binance_tracked', False):
            if not v or v <= 0:
                raise ValueError('amount_to_track is required and must be greater than 0 when binance_tracked is True')
        return v

    @validator('base_pair_uuid')
    def validate_base_pair_not_self(cls, v, values):
        # This validation will be enhanced at the database level
        # to ensure base_pair exists and is not self-referencing
        return v

    @validator('pair_type', pre=True)
    def validate_pair_type(cls, v):
        # Convert string to PairType enum if needed
        if isinstance(v, str):
            try:
                return PairType(v.lower())
            except ValueError:
                raise ValueError(f'Invalid pair_type. Must be one of: {", ".join([pt.value for pt in PairType])}')
        return v

class CurrencyPairCreate(CurrencyPairBase):
    pass

class CurrencyPairUpdate(BaseModel):
    pair_type: Optional[PairType] = None
    base_pair_uuid: Optional[UUID] = None
    derived_percentage: Optional[Decimal] = None
    use_inverse_percentage: Optional[bool] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    is_monitored: Optional[bool] = None
    binance_tracked: Optional[bool] = None
    banks_to_track: Optional[List[str]] = None
    amount_to_track: Optional[Decimal] = None
    usdt_reference_side: Optional[Literal["FROM", "TO"]] = None
    usdt_manual_rate: Optional[float] = None
    usdt_pair_uuid: Optional[UUID] = None
    usdt_pair_inverse: Optional[bool] = None

    @validator('pair_type', pre=True)
    def validate_pair_type(cls, v):
        # Convert string to PairType enum if needed
        if v is not None and isinstance(v, str):
            try:
                return PairType(v.lower())
            except ValueError:
                raise ValueError(f'Invalid pair_type. Must be one of: {", ".join([pt.value for pt in PairType])}')
        return v

class CurrencyPairResponse(BaseModel):
    uuid: UUID
    pair_symbol: str
    pair_type: PairType
    from_currency_uuid: Optional[UUID] = None
    to_currency_uuid: Optional[UUID] = None
    base_pair_uuid: Optional[UUID] = None
    derived_percentage: Optional[Decimal] = None
    use_inverse_percentage: bool
    from_currency: Optional[CurrencyResponse] = None
    to_currency: Optional[CurrencyResponse] = None
    base_pair: Optional['CurrencyPairResponse'] = None
    display_name: str
    description: Optional[str] = None
    is_active: bool
    is_monitored: bool
    binance_tracked: bool
    banks_to_track: Optional[List[str]] = None
    amount_to_track: Optional[Decimal] = None
    usdt_reference_side: Optional[str] = None
    usdt_manual_rate: Optional[float] = None
    usdt_pair_uuid: Optional[UUID] = None
    usdt_pair_symbol: Optional[str] = None
    usdt_pair_inverse: bool = False
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        use_enum_values = True

class CurrencyPairList(BaseModel):
    pairs: list[CurrencyPairResponse]
    total: int
    skip: int
    limit: int

class CurrencyPairStatusUpdate(BaseModel):
    is_active: Optional[bool] = None
    is_monitored: Optional[bool] = None
    binance_tracked: Optional[bool] = None
    banks_to_track: Optional[List[str]] = None
    amount_to_track: Optional[Decimal] = None

class CurrencyPairPercentageUpdate(BaseModel):
    derived_percentage: float = Field(..., ge=0, le=100)
    use_inverse_percentage: bool = False

class CurrencyPairStats(BaseModel):
    total_pairs: int
    active_pairs: int
    monitored_pairs: int
    pairs_by_currency: dict

# Forward reference resolution for self-referencing models
CurrencyPairResponse.model_rebuild()