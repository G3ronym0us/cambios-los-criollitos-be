from pydantic import BaseModel, Field, validator
from datetime import datetime
from typing import Optional, List, Literal
from uuid import UUID


# ===== Client =====

class WhatsAppClientUpsert(BaseModel):
    """Crear o actualizar parcialmente un cliente. Todos los campos opcionales
    excepto que la ruta ya fija el `phone`."""
    display_name: Optional[str] = None
    preferred_pair_uuid: Optional[UUID] = None
    is_tracked: Optional[bool] = None
    is_blocked: Optional[bool] = None
    is_usdt_authorized: Optional[bool] = None


class WhatsAppClientResponse(BaseModel):
    uuid: UUID
    phone: str
    display_name: Optional[str] = None
    preferred_pair_uuid: Optional[UUID] = None
    preferred_pair_symbol: Optional[str] = None
    is_tracked: bool
    is_blocked: bool
    is_usdt_authorized: bool
    last_seen_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ===== Operation =====

class WhatsAppOperationCreate(BaseModel):
    """
    Crear una cotización desde el bot.

    El bot solo manda lo que extrajo del mensaje: teléfono, currencies,
    amount y side. El backend resuelve la tasa, aplica BCV si corresponde
    y crea el registro.
    """
    client_phone: str = Field(..., min_length=4, max_length=32)
    client_display_name: Optional[str] = None
    from_currency: str = Field(..., min_length=2, max_length=10)
    to_currency: str = Field(..., min_length=2, max_length=10)
    amount: float = Field(..., gt=0)
    amount_side: Literal["SEND", "RECEIVE"] = "SEND"
    margin_override: Optional[float] = Field(None, ge=0, le=99)
    notes: Optional[str] = None  # payment_info del bot (cédula, banco, etc.)

    @validator('from_currency', 'to_currency')
    def upper_currency(cls, v: str) -> str:
        return v.upper()


class WhatsAppOperationApprove(BaseModel):
    notes: Optional[str] = None


class WhatsAppOperationCancel(BaseModel):
    reason: Optional[str] = None


class WhatsAppOperationComplete(BaseModel):
    notes: Optional[str] = None
    # Si la op es venta de USD efectivo y el operador aún no recibió los billetes,
    # marcar delivery_status=PENDING para tracking de entregas.
    pending_delivery: bool = False
    commission_config_uuid: Optional[UUID] = None
    skip_fund: bool = False


class WhatsAppOperationResponse(BaseModel):
    uuid: UUID
    client_uuid: Optional[UUID] = None
    client_phone: Optional[str] = None
    client_display_name: Optional[str] = None
    currency_pair_uuid: Optional[UUID] = None
    pair_symbol: Optional[str] = None
    from_currency: Optional[str] = None
    to_currency: Optional[str] = None
    from_amount: float
    to_amount: float
    rate_used: float
    inverse_percentage: bool
    applied_percentage: Optional[float] = None
    default_percentage: Optional[float] = None
    amount_side: Literal["SEND", "RECEIVE"]
    bcv_usd: Optional[float] = None
    status: Literal["QUOTED", "PENDING", "COMPLETED", "CANCELLED"]
    delivery_status: Optional[Literal["PENDING", "RECEIVED"]] = None
    delivered_at: Optional[datetime] = None
    notes: Optional[str] = None
    transaction_uuid: Optional[UUID] = None
    legacy_sqlite_id: Optional[str] = None
    quoted_at: datetime
    expires_at: datetime
    approved_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WhatsAppOperationList(BaseModel):
    operations: List[WhatsAppOperationResponse]
    total: int


# ===== BCV =====

class BcvRateResponse(BaseModel):
    rate: float
    source: str
    fetched_at: datetime

    class Config:
        from_attributes = True
