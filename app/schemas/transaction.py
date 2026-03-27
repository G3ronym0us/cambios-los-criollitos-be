from pydantic import BaseModel, validator, Field
from datetime import datetime
from typing import Optional, List
from uuid import UUID

# ===== Profit Split Schemas =====

class ProfitSplitBase(BaseModel):
    """Schema base para división de ganancias"""
    user_uuid: UUID
    profit_percentage: float = Field(..., ge=0, le=100, description="Porcentaje de ganancia asignado (0-100)")

class ProfitSplitCreate(ProfitSplitBase):
    """Schema para crear una división de ganancia"""
    settlement_currency: Optional[str] = None  # Moneda de liquidación. NULL = usar preferred del usuario o USDT
    usdt_rate: Optional[float] = None           # Tasa USDT/moneda_origen para calcular profit_amount_usdt

class ProfitSplitResponse(ProfitSplitBase):
    """Schema de respuesta para división de ganancia"""
    uuid: UUID
    transaction_uuid: UUID
    profit_amount: float
    profit_amount_usdt: Optional[float] = None
    settlement_currency: Optional[str] = None
    settlement_amount: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True

# ===== Transaction Schemas =====

class TransactionBase(BaseModel):
    """Schema base para transacciones"""
    currency_pair_uuid: UUID
    from_amount: float
    to_amount: Optional[float] = None
    exchange_rate: Optional[float] = None
    description: Optional[str] = None
    transaction_type: str = "conversion"
    total_profit_percentage: float = 0.0

    @validator('from_amount')
    def validate_from_amount(cls, v):
        if v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v

    @validator('to_amount')
    def validate_to_amount(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Amount must be greater than 0')
        return v

    @validator('exchange_rate')
    def validate_exchange_rate(cls, v):
        if v is not None and v <= 0:
            raise ValueError('Exchange rate must be greater than 0')
        return v

    @validator('total_profit_percentage')
    def validate_profit_percentage(cls, v):
        if v < 0 or v > 100:
            raise ValueError('Profit percentage must be between 0 and 100')
        return v

class TransactionCreate(TransactionBase):
    """
    Schema para crear transacción con distribución de ganancias

    Puede usar una configuración predefinida (commission_config_uuid) o splits manuales (profit_splits)
    """
    user_uuid: Optional[UUID] = None
    commission_config_uuid: Optional[UUID] = Field(None, description="UUID de configuración predefinida de comisiones")
    profit_splits: Optional[List[ProfitSplitCreate]] = Field(None, description="Splits manuales (alternativa a config_uuid)")
    force: bool = Field(False, description="Forzar creación ignorando advertencias de duplicados")
    usdt_rate: Optional[float] = None  # Tasa USDT/moneda_origen al momento de la transacción
    fund_group_uuid: Optional[UUID] = Field(None, description="UUID del fondo al que aplica esta transacción. Overridea el fondo de la config si se especifica. Para splits manuales, permite vincular al fondo")
    skip_fund: bool = Field(False, description="Si True, no crea movimiento de fondo aunque la config tenga uno configurado")

    @validator('profit_splits')
    def validate_profit_splits(cls, v, values):
        """Validar que la suma de porcentajes coincida con el total"""
        # Si se usa config_uuid, profit_splits debe estar vacío
        if values.get('commission_config_uuid') and v:
            raise ValueError('Cannot specify both commission_config_uuid and profit_splits')

        # Si no se usa config_uuid, validar splits manuales
        if not values.get('commission_config_uuid') and v and 'total_profit_percentage' in values:
            total_split = sum(split.profit_percentage for split in v)
            total_profit = values['total_profit_percentage']

            # Permitir diferencias pequeñas por redondeo (0.01%)
            if abs(total_split - total_profit) > 0.01:
                raise ValueError(
                    f'Sum of profit splits ({total_split}%) must equal total_profit_percentage ({total_profit}%)'
                )
        return v

class TransactionUpdate(BaseModel):
    """Schema para actualizar transacción"""
    currency_pair_uuid: Optional[UUID] = None
    from_amount: Optional[float] = None
    to_amount: Optional[float] = None
    exchange_rate: Optional[float] = None
    description: Optional[str] = None
    transaction_type: Optional[str] = None
    total_profit_percentage: Optional[float] = None
    status: Optional[str] = None
    usdt_rate: Optional[float] = None  # Para recalcular profit_amount_usdt de splits al actualizar

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

    @validator('total_profit_percentage')
    def validate_profit_percentage(cls, v):
        if v is not None and (v < 0 or v > 100):
            raise ValueError('Profit percentage must be between 0 and 100')
        return v

class TransactionResponse(BaseModel):
    """Schema de respuesta para transacción"""
    uuid: UUID
    currency_pair_uuid: Optional[UUID] = None
    pair_symbol: Optional[str] = None  # Incluido para facilidad (ej: "USDT/VES")
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None
    from_amount: float
    to_amount: Optional[float] = None
    exchange_rate: Optional[float] = None
    description: Optional[str] = None
    transaction_type: str = "conversion"
    total_profit_percentage: float = 0.0
    user_uuid: Optional[UUID] = None
    profit_amount: float
    profit_amount_usdt: Optional[float] = None
    status: str
    created_at: datetime
    updated_at: Optional[datetime]
    completed_at: Optional[datetime]
    profit_splits: List[ProfitSplitResponse] = []

    class Config:
        from_attributes = True

class TransactionList(BaseModel):
    """Schema para lista paginada de transacciones"""
    transactions: List[TransactionResponse]
    total: int
    page: int
    per_page: int
    total_pages: int

class SimilarTransactionWarning(BaseModel):
    """Schema para advertencia de transacción similar/duplicada"""
    warning: str = "Similar transaction found"
    similar_transaction: TransactionResponse
    requires_confirmation: bool = True
    message: str

# ===== Report Schemas =====

class UserProfitReport(BaseModel):
    """Reporte de ganancias por usuario"""
    user_uuid: UUID
    username: Optional[str] = None
    email: Optional[str] = None
    total_profit: float
    transaction_count: int
    transactions: List[TransactionResponse] = []
    page: int = 1
    per_page: int = 50
    total_pages: int = 0

class ProfitSummary(BaseModel):
    """Resumen general de ganancias"""
    total_profit: float
    total_transactions: int
    by_currency_pair: dict
    by_user: List[UserProfitReport]
    date_range: Optional[dict] = None