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
    rounding_mode: Optional[Literal["RATE", "AMOUNT"]] = Field(None, description="Quote rounding: 'RATE' rounds the per-unit rate, 'AMOUNT' rounds a side's amount, null disables it")
    rounding_step: Optional[Decimal] = Field(None, description="Multiple to round to (e.g. 100, 5)")
    rounding_direction: Optional[Literal["UP", "DOWN"]] = Field(None, description="Rounding direction")
    rounding_amount_side: Optional[Literal["FROM", "TO"]] = Field(None, description="AMOUNT mode only: which side's amount is rounded (rounded only when it is the calculated side)")
    negotiation_step: Optional[Decimal] = Field(None, description="Multiple this pair is negotiated in (e.g. 10000). Not applied automatically: only suggests round amounts when an operator creates a quote by hand")
    negotiation_step_side: Optional[Literal["FROM", "TO"]] = Field(None, description="Which of the pair's currencies negotiation_step is expressed in")

    # Un par NO tiene por qué cruzar dos monedas distintas: `USDT-USDT` es una paridad 1:1 y
    # es la única forma de expresar «un porcentaje sobre la par» en un modelo donde todo
    # porcentaje necesita colgarse de un par base (ZELLE-USDT = USDT-USDT −7 %). No abre
    # ninguna cotización rara: el resolver corta en seco cuando `from == to` y devuelve 1.
    # Por eso aquí ya no se valida que sean diferentes.

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

    # `always=True` o el validador se salta justo cuando el lado viene ausente,
    # que es el caso que existe para rechazar. Sin él, un `rounding_mode="AMOUNT"`
    # sin lado se guardaba y el bot acababa sin saber qué monto redondear.
    @validator('rounding_amount_side', always=True)
    def validate_rounding_config(cls, v, values):
        mode = values.get('rounding_mode')
        if mode is not None:
            if not values.get('rounding_step') or values['rounding_step'] <= 0:
                raise ValueError('rounding_step is required and must be > 0 when rounding_mode is set')
            if not values.get('rounding_direction'):
                raise ValueError('rounding_direction is required when rounding_mode is set')
            if mode == 'AMOUNT' and v is None:
                raise ValueError("rounding_amount_side is required when rounding_mode is 'AMOUNT'")
        return v

    # `always=True` es imprescindible: sin él el validador no corre cuando el
    # lado viene ausente, que es justo el caso que hay que rechazar.
    @validator('negotiation_step_side', always=True)
    def validate_negotiation_step(cls, v, values):
        # Declared after negotiation_step, so it is already in `values`.
        step = values.get('negotiation_step')
        if step is not None:
            if step <= 0:
                raise ValueError('negotiation_step must be > 0')
            if v is None:
                raise ValueError('negotiation_step_side is required when negotiation_step is set')
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
    rounding_mode: Optional[Literal["RATE", "AMOUNT"]] = None
    rounding_step: Optional[Decimal] = None
    rounding_direction: Optional[Literal["UP", "DOWN"]] = None
    rounding_amount_side: Optional[Literal["FROM", "TO"]] = None
    negotiation_step: Optional[Decimal] = None
    negotiation_step_side: Optional[Literal["FROM", "TO"]] = None

    # Igual que arriba: el detalle guarda por aquí, así que sin esta copia la
    # regla no correría en el camino que de hecho edita el redondeo.
    #
    # Solo exige la config completa cuando el payload trae `rounding_mode`. Un
    # update parcial que toque otra cosa —o solo el múltiplo— no dispara nada:
    # aquí no hay acceso al par guardado, así que exigir campos que el llamante
    # no está cambiando rechazaría peticiones legítimas.
    @validator('rounding_amount_side', always=True)
    def validate_rounding_config(cls, v, values):
        mode = values.get('rounding_mode')
        if mode is not None:
            if not values.get('rounding_step') or values['rounding_step'] <= 0:
                raise ValueError('rounding_step is required and must be > 0 when rounding_mode is set')
            if not values.get('rounding_direction'):
                raise ValueError('rounding_direction is required when rounding_mode is set')
            if mode == 'AMOUNT' and v is None:
                raise ValueError("rounding_amount_side is required when rounding_mode is 'AMOUNT'")
        return v

    # El detalle del par guarda por aquí, no por `CurrencyPairCreate`: sin esta
    # copia la validación de arriba no cubriría el único camino que se usa.
    @validator('negotiation_step_side', always=True)
    def validate_negotiation_step(cls, v, values):
        step = values.get('negotiation_step')
        if step is not None:
            if step <= 0:
                raise ValueError('negotiation_step must be > 0')
            if v is None:
                raise ValueError('negotiation_step_side is required when negotiation_step is set')
        return v

    @validator('pair_type', pre=True)
    def validate_pair_type(cls, v):
        # Convert string to PairType enum if needed
        if v is not None and isinstance(v, str):
            try:
                return PairType(v.lower())
            except ValueError:
                raise ValueError(f'Invalid pair_type. Must be one of: {", ".join([pt.value for pt in PairType])}')
        return v

class CurrencyPairRateInfo(BaseModel):
    """
    Tasa vigente de un par, embebida en el listado.

    El admin necesita la tasa como columna principal; pedirla par por par con
    `/rates/by-pair/{uuid}` disparaba una llamada por fila. Se resuelve en lote
    en el repositorio (ver `get_rate_info_for_pairs`).
    """
    rate: float
    is_manual: bool = False
    automatic_rate: Optional[float] = None
    # Cuándo se leyó la tasa: cada corrida del monitor inserta una fila nueva,
    # así que el `created_at` de la fila activa es la antigüedad de la tasa.
    read_at: datetime
    rate_24h_ago: Optional[float] = None
    change_24h_percentage: Optional[float] = None


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
    rounding_mode: Optional[str] = None
    rounding_step: Optional[Decimal] = None
    rounding_direction: Optional[str] = None
    rounding_amount_side: Optional[str] = None
    negotiation_step: Optional[Decimal] = None
    negotiation_step_side: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    # Solo lo llenan los endpoints de listado y de detalle; el resto lo deja en None.
    current_rate: Optional[CurrencyPairRateInfo] = None

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