from pydantic import BaseModel, validator, Field
from datetime import datetime
from typing import Optional, List
from uuid import UUID


# ===== Fund Group Schemas =====

class FundGroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    currency: str = Field(..., min_length=1, max_length=10)
    description: Optional[str] = None
    # JID del grupo de WhatsApp (...@g.us) para resolver el FundGroup desde el bot.
    whatsapp_group_jid: Optional[str] = Field(None, max_length=64)


class FundGroupUpdate(BaseModel):
    """Actualización parcial de un grupo (de momento solo el JID de WhatsApp)."""
    whatsapp_group_jid: Optional[str] = Field(None, max_length=64)
    clear_whatsapp_group_jid: bool = False


class FundGroupMemberCreate(BaseModel):
    user_uuid: UUID
    is_fund_manager: bool = False
    # Número de WhatsApp del socio: activa la detección automática del escenario VIA_PARTNER.
    whatsapp_phone: Optional[str] = Field(None, max_length=32)


class FundGroupMemberUpdate(BaseModel):
    is_fund_manager: Optional[bool] = None
    whatsapp_phone: Optional[str] = Field(None, max_length=32)
    clear_whatsapp_phone: bool = False


class FundGroupMemberResponse(BaseModel):
    uuid: UUID
    user_uuid: UUID
    username: Optional[str] = None
    is_fund_manager: bool
    whatsapp_phone: Optional[str] = None
    joined_at: datetime

    class Config:
        from_attributes = True


class FundGroupResponse(BaseModel):
    uuid: UUID
    name: str
    currency: str
    description: Optional[str] = None
    is_active: bool
    whatsapp_group_jid: Optional[str] = None
    created_at: datetime
    members: List[FundGroupMemberResponse] = []

    class Config:
        from_attributes = True


# ===== Fund Movement Schemas =====

VALID_MOVEMENT_TYPES = {"DEPOSIT", "EXCHANGE", "PERSONAL", "ADJUSTMENT"}


class FundMovementCreate(BaseModel):
    group_uuid: UUID
    user_uuid: UUID
    movement_type: str = Field(..., description="DEPOSIT | EXCHANGE | PERSONAL | ADJUSTMENT")
    amount: float = Field(..., gt=0, description="Monto positivo del movimiento")
    currency: str = Field(..., min_length=1, max_length=10)
    amount_usdt: Optional[float] = None
    usdt_rate: Optional[float] = None
    transaction_uuid: Optional[UUID] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    movement_date: datetime

    @validator("movement_type")
    def validate_movement_type(cls, v):
        v_upper = v.upper()
        if v_upper not in VALID_MOVEMENT_TYPES:
            raise ValueError(f"movement_type must be one of: {', '.join(VALID_MOVEMENT_TYPES)}")
        return v_upper


class FundMovementResponse(BaseModel):
    uuid: UUID
    group_uuid: Optional[UUID] = None
    group_name: Optional[str] = None
    user_uuid: Optional[UUID] = None
    username: Optional[str] = None
    movement_type: str
    amount: float
    currency: str
    amount_usdt: Optional[float] = None
    usdt_rate: Optional[float] = None
    transaction_uuid: Optional[UUID] = None
    reference: Optional[str] = None
    notes: Optional[str] = None
    recorded_by_uuid: Optional[UUID] = None
    recorded_by_username: Optional[str] = None
    movement_date: datetime
    created_at: datetime

    class Config:
        from_attributes = True


# ===== Position & Balance Schemas =====

class UserPositionResponse(BaseModel):
    """Posición individual de un gestor dentro de un grupo de fondo"""
    user_uuid: UUID
    username: Optional[str] = None
    group_uuid: UUID
    group_name: str
    is_fund_manager: bool
    total_deposited: float        # Suma de depósitos en moneda nativa
    total_deposited_usdt: float   # Suma de depósitos en USDT
    total_outflow: float          # Suma de EXCHANGE + PERSONAL en moneda nativa
    total_outflow_usdt: float     # Suma de EXCHANGE + PERSONAL en USDT
    position: float               # total_deposited - total_outflow (moneda nativa)
    position_usdt: float          # deposited_usdt - outflow_usdt
    currency: str                 # Moneda del fondo


class FundGroupBalanceResponse(BaseModel):
    """
    Balance consolidado del grupo — replica las tres columnas de la hoja Excel:
    - total_position_usdt  → "Total"     (depósitos - salidas)
    - total_profit_usdt    → "Acumulada" (ganancias de transacciones completadas)
    - available_funds_usdt → "Fondos"    (Acumulada - Total)
    """
    group_uuid: UUID
    group_name: str
    currency: str
    total_deposited_usdt: float
    total_outflow_usdt: float
    total_position_usdt: float       # deposited - outflow → "Total" Excel
    total_profit_usdt: float         # suma profit splits de miembros COMPLETED → "Acumulada"
    available_funds_usdt: float      # profit + position → "Fondos" Excel
    by_member: List[UserPositionResponse] = []


class FundMovementList(BaseModel):
    movements: List[FundMovementResponse]
    total: int
    page: int
    per_page: int
    total_pages: int


# ===== Pending Deposits (detectados por el bot, confirmables desde /admin/funds) =====

class FundPendingDepositResponse(BaseModel):
    uuid: UUID
    group_uuid: Optional[UUID] = None
    group_name: Optional[str] = None
    detected_user_uuid: Optional[UUID] = None
    detected_username: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    provider: Optional[str] = None
    reference: Optional[str] = None
    raw_text: Optional[str] = None
    status: str
    confirmed_movement_uuid: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class FundPendingDepositConfirm(BaseModel):
    """Confirmar un depósito pendiente → crea un FundMovement DEPOSIT."""
    deposit_method: str = Field("ZELLE", description="ZELLE | BINANCE | KRAKEN | TRANSFER | OTHER")
    amount: Optional[float] = Field(None, gt=0)       # override del monto detectado
    currency: Optional[str] = None                    # override de la moneda detectada
    user_uuid: Optional[UUID] = None                  # depositante (default: gestor detectado)
    reference: Optional[str] = None
    notes: Optional[str] = None

    @validator('deposit_method')
    def validate_method(cls, v: str) -> str:
        allowed = {"ZELLE", "BINANCE", "KRAKEN", "TRANSFER", "OTHER"}
        v_up = v.upper()
        if v_up not in allowed:
            raise ValueError(f"deposit_method must be one of: {', '.join(sorted(allowed))}")
        return v_up
