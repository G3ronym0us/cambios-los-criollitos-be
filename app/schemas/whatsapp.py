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
    scenario: Literal["NORMAL", "ZELLE_DIRECT", "VIA_PARTNER"] = "NORMAL"
    fund_group_uuid: Optional[UUID] = None
    fund_group_name: Optional[str] = None
    received_by_user_uuid: Optional[UUID] = None
    received_by_username: Optional[str] = None
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
    # Marcas de vínculo con pagos (inyectadas por el router de operaciones).
    has_incoming_payment: bool = False
    has_outgoing_payment: bool = False

    class Config:
        from_attributes = True


class WhatsAppOperationScenarioUpdate(BaseModel):
    """
    Setear/editar el escenario, grupo y receptor del entrante de una operación.
    Todos opcionales (PATCH parcial). El grupo se resuelve por `fund_group_uuid` o,
    para el bot, por `group_jid` (FundGroup.whatsapp_group_jid).
    """
    scenario: Optional[Literal["NORMAL", "ZELLE_DIRECT", "VIA_PARTNER"]] = None
    fund_group_uuid: Optional[UUID] = None
    group_jid: Optional[str] = None
    received_by_user_uuid: Optional[UUID] = None
    # Permite explícitamente limpiar el grupo / receptor (poner a NULL) cuando True.
    clear_fund_group: bool = False
    clear_received_by: bool = False
    # Reasigna la op a un cliente anónimo dedicado (VIA_PARTNER: el socio no es el cliente).
    anonymize_client: bool = False


class WhatsAppPartnerResponse(BaseModel):
    """Socio (FundGroupMember con whatsapp_phone) que reporta entrantes desde su número."""
    whatsapp_phone: str
    user_uuid: UUID
    username: Optional[str] = None
    group_uuid: UUID
    group_name: str
    group_jid: Optional[str] = None
    is_fund_manager: bool = False


class WhatsAppPartnerList(BaseModel):
    partners: List[WhatsAppPartnerResponse]
    total: int


class WhatsAppPendingDepositCreate(BaseModel):
    """El bot reporta un comprobante subido al grupo por un gestor → depósito PENDING."""
    group_jid: str
    detected_phone: Optional[str] = None     # autor del mensaje en el grupo (gestor)
    amount: Optional[float] = None
    currency: Optional[str] = None
    provider: Optional[str] = None
    reference: Optional[str] = None
    raw_text: Optional[str] = None


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
    irrelevant_description: Optional[str] = None


class WhatsAppForwardToGroup(BaseModel):
    """Marcar un pago entrante como contabilizado en un grupo (escenario ZELLE_DIRECT)."""
    group_jid: Optional[str] = None
    group_uuid: Optional[UUID] = None


class WhatsAppPaymentDeposit(BaseModel):
    """Registrar un pago entrante como depósito (FundMovement DEPOSIT) a un fondo."""
    group_uuid: UUID
    user_uuid: UUID  # depositante (gestor)
    amount: float = Field(..., gt=0)
    currency: str = Field(..., min_length=1, max_length=10)
    deposit_method: str = Field(..., description="ZELLE | BINANCE | KRAKEN | TRANSFER | OTHER")
    reference: Optional[str] = None
    notes: Optional[str] = None

    @validator('deposit_method')
    def validate_method(cls, v: str) -> str:
        allowed = {"ZELLE", "BINANCE", "KRAKEN", "TRANSFER", "OTHER"}
        v_up = v.upper()
        if v_up not in allowed:
            raise ValueError(f"deposit_method must be one of: {', '.join(sorted(allowed))}")
        return v_up


class WhatsAppCreateOpFromPayment(BaseModel):
    from_currency: str = Field(..., min_length=2, max_length=10)
    to_currency: str = Field(..., min_length=2, max_length=10)
    from_amount: float = Field(..., gt=0)
    to_amount: float = Field(..., gt=0)

    @validator('from_currency', 'to_currency')
    def upper_currency(cls, v: str) -> str:
        return v.upper()


class WhatsAppCreateOpManual(BaseModel):
    """Crear operación a mano desde un pago (operador). Soporta dirección y fondo (+EXCHANGE)."""
    from_currency: str = Field(..., min_length=2, max_length=10)
    to_currency: str = Field(..., min_length=2, max_length=10)
    from_amount: float = Field(..., gt=0)
    to_amount: float = Field(..., gt=0)
    amount_side: str = "SEND"
    fund_group_uuid: Optional[UUID] = None
    exchange_user_uuid: Optional[UUID] = None

    @validator('from_currency', 'to_currency')
    def upper_currency(cls, v: str) -> str:
        return v.upper()

    @validator('amount_side')
    def validate_side(cls, v: str) -> str:
        v_up = v.upper()
        if v_up not in {"SEND", "RECEIVE"}:
            raise ValueError("amount_side must be SEND or RECEIVE")
        return v_up


class WhatsAppIncomingPaymentResponse(BaseModel):
    id: int
    uuid: UUID
    client_phone: str
    client_name: Optional[str] = None
    client_uuid: Optional[UUID] = None
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
    irrelevant_description: Optional[str] = None
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
