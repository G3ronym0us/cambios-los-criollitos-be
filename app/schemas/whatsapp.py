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


class WhatsAppOperationNotes(BaseModel):
    """Adjuntar/actualizar las notas (datos de pago) de una op activa.

    Espejo de `updateOperationStatus(id, 'QUOTED'|'PENDING', { notes })` del bot:
    reemplaza `notes` y, si `set_pending`, transiciona QUOTED→PENDING.
    """
    notes: str = Field(..., min_length=1)
    set_pending: bool = False


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


class WhatsAppStatsResponse(BaseModel):
    pending: int
    completed: int
    quoted: int
    cancelled: int
    completed_today: int


# ===== Payments (comprobantes OCR) =====

class WhatsAppPaymentCreate(BaseModel):
    """Crear un pago (incoming u outgoing). Espejo de save*Payment del bot."""
    client_phone: str = Field(..., min_length=3, max_length=64)
    raw_text: Optional[str] = None
    operation_uuid: Optional[UUID] = None
    provider: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    bank_from: Optional[str] = None
    bank_to: Optional[str] = None
    account_number: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    reference: Optional[str] = None
    # Solo outgoing: cadena de reenvío (Zelle entrante reenviado al grupo).
    source_payment_id: Optional[int] = None


class WhatsAppPaymentUpdate(BaseModel):
    """Editar campos de un pago (correction tracking). Todos opcionales."""
    provider: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    bank_from: Optional[str] = None
    bank_to: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    reference: Optional[str] = None


class WhatsAppPaymentLink(BaseModel):
    operation_uuid: Optional[UUID] = None


class WhatsAppPersonalExpense(BaseModel):
    is_personal_expense: bool
    personal_description: Optional[str] = None


class WhatsAppIrrelevant(BaseModel):
    is_irrelevant: bool


class WhatsAppCreateOpFromPayment(BaseModel):
    from_currency: str = Field(..., min_length=2, max_length=10)
    to_currency: str = Field(..., min_length=2, max_length=10)
    from_amount: float = Field(..., gt=0)
    to_amount: float = Field(..., gt=0)

    @validator('from_currency', 'to_currency')
    def upper_currency(cls, v: str) -> str:
        return v.upper()


class WhatsAppIncomingPaymentResponse(BaseModel):
    id: int
    uuid: UUID
    client_phone: str
    client_name: Optional[str] = None
    provider: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    bank_from: Optional[str] = None
    bank_to: Optional[str] = None
    account_number: Optional[str] = None
    identification: Optional[str] = None
    phone_to: Optional[str] = None
    reference: Optional[str] = None
    raw_text: Optional[str] = None
    operation_uuid: Optional[UUID] = None
    corrected_at: Optional[datetime] = None
    correction_original: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class WhatsAppOutgoingPaymentResponse(WhatsAppIncomingPaymentResponse):
    is_personal_expense: int = 0
    personal_description: Optional[str] = None
    is_irrelevant: int = 0
    source_payment_id: Optional[int] = None


class WhatsAppIncomingPaymentList(BaseModel):
    payments: List[WhatsAppIncomingPaymentResponse]
    total: int


class WhatsAppOutgoingPaymentList(BaseModel):
    payments: List[WhatsAppOutgoingPaymentResponse]
    total: int


class WhatsAppCorrectedPayment(BaseModel):
    table: str  # "incoming_payments" | "outgoing_payments"
    id: int
    client_phone: str
    created_at: datetime
    corrected_at: datetime
    original: dict
    corrected: dict


# ===== BCV =====

class BcvRateResponse(BaseModel):
    rate: float
    source: str
    fetched_at: datetime

    class Config:
        from_attributes = True
