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
    preferred_pair_symbol: Optional[str] = Field(None, min_length=3, max_length=20)
    is_tracked: Optional[bool] = None
    is_blocked: Optional[bool] = None
    is_usdt_authorized: Optional[bool] = None
    is_rate_setter: Optional[bool] = None
    default_payment_info: Optional[str] = None
    default_payment_currency: Optional[str] = None

    @validator("preferred_pair_symbol")
    def normalize_preferred_pair_symbol(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else value


class WhatsAppClientResponse(BaseModel):
    uuid: UUID
    phone: str
    display_name: Optional[str] = None
    preferred_pair_uuid: Optional[UUID] = None
    preferred_pair_symbol: Optional[str] = None
    is_tracked: bool
    is_blocked: bool
    is_usdt_authorized: bool
    is_rate_setter: bool = False
    default_payment_info: Optional[str] = None
    default_payment_currency: Optional[str] = None
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
    # Monto USD original cuando el bot ancla el monto a recibir a la tasa BCV.
    # `amount` ya contiene el monto efectivo a cotizar (normalmente VES).
    bcv_usd: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None  # payment_info del bot (cédula, banco, etc.)
    # Beneficiario nombrado en el mensaje ("465000 a yelitza"). El bot manda el nombre
    # crudo; `beneficiary_ambiguous` viene en true cuando la resolución encontró varias
    # cuentas con ese nombre, para que el backend no aprenda una tercera después.
    beneficiary_alias: Optional[str] = Field(None, max_length=120)
    beneficiary_ambiguous: bool = False

    @validator('from_currency', 'to_currency')
    def upper_currency(cls, v: str) -> str:
        return v.upper()


class WhatsAppOperationApprove(BaseModel):
    notes: Optional[str] = None


class WhatsAppOperationCancel(BaseModel):
    reason: Optional[str] = None


class WhatsAppOperationStatusUpdate(BaseModel):
    status: Literal["QUOTED", "PENDING", "COMPLETED", "CANCELLED"]


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
    # Valor del trato: lo que entrega el cliente, con sus equivalentes.
    amount: Optional[float] = None
    currency: Optional[str] = None
    delivered_amount: Optional[float] = None
    #: El hueco declarado sin comprobante (efectivo). NO está en `delivered_amount` —que
    #: sólo cuenta comprobantes— pero SÍ está descontado de `pending_amount`. Sin este
    #: campo, una operación de 75 con 40 entregados en efectivo llega al front como si
    #: valiera 35 desde el principio, y los 40 desaparecen de la pantalla.
    uncovered_amount: Optional[float] = None
    uncovered_reason: Optional[str] = None
    pending_amount: Optional[float] = None
    #: La OTRA pata, y sólo dice algo en un par que se cambia en efectivo: cuánto de los
    #: billetes del cliente ya está recogido, y cuánto falta. `pending_amount` mide lo
    #: nuestro —lo que no hemos cubierto— y en esos pares llega a cero en cuanto se vincula
    #: el comprobante en bolívares, sin que nadie haya recogido un dólar. Sin estos dos
    #: campos la pantalla no puede distinguir «ya está pagada» de «ya está cobrada».
    collected_amount: Optional[float] = None
    to_collect: Optional[float] = None
    # Cuántos comprobantes cubren la operación, y la tasa que resulta de ellos
    # (entregado ÷ valor). `rate_used` es la que se cotizó; esta es la que salió.
    payments_count: int = 0
    real_rate: Optional[float] = None
    amount_usdt: Optional[float] = None
    usdt_rate: Optional[float] = None
    bcv_amount: Optional[float] = None
    bcv_rate: Optional[float] = None
    valuation_at: Optional[datetime] = None
    # Cotización prometida (par + montos + tasa).
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
    # Quedó sin ningún comprobante y un operador lo aceptó explícitamente.
    no_payments_ack_by_username: Optional[str] = None
    no_payments_ack_at: Optional[datetime] = None
    no_payments_ack_note: Optional[str] = None
    transaction_uuid: Optional[UUID] = None
    legacy_sqlite_id: Optional[str] = None
    # Las dos fechas que de verdad sitúan la operación en el tiempo, y que no son
    # `created_at`: cuándo entró el dinero del cliente y cuándo salió el nuestro. Una
    # operación que el bot no reconoció se arma a mano días después de las dos.
    first_incoming_payment_at: Optional[datetime] = None
    #: Su par se cambia en efectivo. Entonces `first_incoming_payment_at` vacío no dice que
    #: el cliente no haya pagado: dice que de un billete no hay comprobante que adjuntar.
    settles_in_cash: bool = False
    # Distinto de `first_incoming_payment_at`: ese mide antigüedad (primer pago), este es
    # el pago más reciente — lo que un listado debe mostrar como "fecha del pago" cuando
    # el cliente pagó en varias partes. Ver el docstring de la property en el modelo.
    last_incoming_payment_at: Optional[datetime] = None
    last_outgoing_payment_at: Optional[datetime] = None
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
    beneficiary_alias: Optional[str] = None
    beneficiary_account_uuid: Optional[UUID] = None
    beneficiary_ambiguous: bool = False

    class Config:
        from_attributes = True


class WhatsAppOperationScenarioUpdate(BaseModel):
    """
    Setear/editar el escenario, los fondos y el receptor del entrante de una operación.
    Todos opcionales (PATCH parcial). El fondo de la pata que ENTRA se resuelve por
    `fund_group_uuid` o, para el bot, por `group_jid` (FundGroup.whatsapp_group_jid) o por
    `fund_manager_phone` (el fondo que se lleva en el chat directo con su gestor);
    el de la pata que SALE, por `fund_group_out_uuid`.
    """
    scenario: Optional[Literal["NORMAL", "ZELLE_DIRECT", "VIA_PARTNER"]] = None
    fund_group_uuid: Optional[UUID] = None
    fund_group_out_uuid: Optional[UUID] = None
    group_jid: Optional[str] = None
    #: Chat directo del gestor, para los fondos que no se llevan en un grupo.
    fund_manager_phone: Optional[str] = None
    received_by_user_uuid: Optional[UUID] = None
    # Permite explícitamente limpiar los fondos / receptor (poner a NULL) cuando True.
    clear_fund_group: bool = False
    clear_fund_group_out: bool = False
    clear_received_by: bool = False
    # Reasigna la op a un cliente anónimo dedicado (VIA_PARTNER: el socio no es el cliente).
    anonymize_client: bool = False


def _normalize_client_phone(value: str) -> str:
    """Acepta separadores de presentación, pero persiste únicamente dígitos ASCII."""
    cleaned = value.strip()
    for char in ' +-().':
        cleaned = cleaned.replace(char, '')
    if not cleaned.isascii() or not cleaned.isdigit():
        raise ValueError('El teléfono solo puede contener dígitos')
    if len(cleaned) < 4:
        raise ValueError('Teléfono demasiado corto')
    return cleaned


class WhatsAppOperationUpdate(WhatsAppOperationScenarioUpdate):
    """Edición atómica de los datos administrativos de una operación."""

    currency_pair_uuid: Optional[UUID] = None
    #: Puede ser NEGATIVO: se entregó por encima de la tasa base. Es una pérdida real contra
    #: la referencia y esconderla en un cero sería mentir sobre la operación.
    applied_percentage: Optional[float] = Field(None, gt=-100, lt=99)
    client_phone: Optional[str] = Field(None, min_length=4, max_length=32)
    client_display_name: Optional[str] = Field(None, max_length=120)

    @validator('client_phone', pre=True)
    def normalize_phone(cls, v: Optional[str]) -> Optional[str]:
        return _normalize_client_phone(v) if v is not None else None

    @validator('client_display_name')
    def normalize_display_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return v.strip() or None


class WhatsAppPartnerResponse(BaseModel):
    """Socio (FundGroupMember con whatsapp_phone) que reporta entrantes desde su número."""
    whatsapp_phone: str
    user_uuid: UUID
    username: Optional[str] = None
    group_uuid: UUID
    group_name: str
    # Moneda base del fondo. El bot la necesita para decidir si el comprobante ya trae el
    # monto que vale: un fondo en COP repuesto con un envío en USDT no lo trae.
    group_currency: Optional[str] = None
    # Sin jid, el fondo no se lleva en un grupo sino en el chat directo con este gestor.
    group_jid: Optional[str] = None
    is_fund_manager: bool = False


class WhatsAppPartnerList(BaseModel):
    partners: List[WhatsAppPartnerResponse]
    total: int


class WhatsAppPendingDepositCreate(BaseModel):
    """
    El bot reporta un comprobante de un gestor → depósito PENDING.

    Llega por el grupo (`group_jid`) o por el chat directo con el gestor (`manager_phone`):
    no todo fondo se lleva en un grupo de WhatsApp. Hace falta uno de los dos.
    """
    group_jid: Optional[str] = None
    manager_phone: Optional[str] = None
    detected_phone: Optional[str] = None     # autor del mensaje en el grupo (gestor)
    amount: Optional[float] = None
    currency: Optional[str] = None
    provider: Optional[str] = None
    reference: Optional[str] = None
    raw_text: Optional[str] = None

    @validator("manager_phone", always=True)
    def uno_de_los_dos(cls, v, values):
        if not v and not values.get("group_jid"):
            raise ValueError("hace falta group_jid o manager_phone")
        return v


class WhatsAppOperationList(BaseModel):
    operations: List[WhatsAppOperationResponse]
    # El total tras los filtros, no el tamaño de la página.
    total: int
    page: int = 1
    limit: Optional[int] = None


class WhatsAppStatsResponse(BaseModel):
    pending: int
    completed: int
    quoted: int
    cancelled: int
    completed_today: int
    # Lo accionable: cuentan TODO, no la página. El listado los usa como filtros.
    to_settle: int = 0
    to_settle_amount: float = 0
    to_deliver: int = 0
    to_deliver_oldest_at: Optional[datetime] = None
    without_client: int = 0
    expiring: int = 0
    expiring_next_at: Optional[datetime] = None


# ===== De qué mensaje nació la operación =====

class WhatsAppSourceMessage(BaseModel):
    """El mensaje del cliente que originó una cotización."""
    wa_message_id: str = Field(..., min_length=1, max_length=255)
    client_phone: str = Field(..., min_length=3, max_length=64)


class WhatsAppSourceMessageLookup(BaseModel):
    """Ids candidatos de un mismo mensaje; gana el primero que exista."""
    wa_message_ids: List[str] = Field(..., min_length=1, max_length=10)


class WhatsAppSourceMessageResponse(BaseModel):
    """La operación que originó el mensaje, o todo en null si ninguno figura."""
    operation_uuid: Optional[UUID] = None
    client_phone: Optional[str] = None
    wa_message_id: Optional[str] = None


# ===== Bitácora del analizador (corpus) =====

class WhatsAppAnalysisLog(BaseModel):
    """
    Una corrida del analizador de mensajes. El bot la manda sin esperar respuesta: no
    alimenta ninguna decisión, es el corpus con el que después se mide y se entrena.
    """
    client_phone: str = Field(..., min_length=3, max_length=64)
    # La ventana tal cual la vio el analizador, el mensaje más viejo primero.
    messages: List[str] = Field(..., min_length=1, max_length=10)
    # El AnalysisResult crudo. Sin esquema fijo a propósito: cuando el analizador cambie de
    # forma, las filas viejas siguen siendo legibles y `analyzer` dice cuál las produjo.
    output: dict
    wa_message_id: Optional[str] = Field(None, max_length=255)
    analyzer: str = Field("heuristic-v1", min_length=1, max_length=32)
    context: Optional[dict] = None


class WhatsAppAnalysisLogResponse(BaseModel):
    uuid: UUID


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


class WhatsAppOperationValue(BaseModel):
    """Corregir cuánto vale el trato (sube y baja)."""
    amount: float = Field(..., gt=0)


class OutgoingCoverage(BaseModel):
    """Cuánto del valor de la operación cubre este comprobante de salida."""
    settled_amount: Optional[float] = Field(None, gt=0)


class PaymentAllocationItem(BaseModel):
    """Parte de un pago entrante que respalda a una operación (en la moneda del pago)."""
    operation_uuid: UUID
    amount: float = Field(..., gt=0)


class PaymentAllocationsUpdate(BaseModel):
    """
    Reparto completo del pago: reemplaza el anterior. La suma no puede pasarse del monto del
    pago (contando lo ya acreditado al saldo del cliente) ni quedar vacía.
    """
    allocations: List[PaymentAllocationItem]


class OutgoingSettlementItem(BaseModel):
    """
    Parte del valor de UNA operación que cubre un comprobante de salida.

    El monto va en la moneda del VALOR de esa operación (80 ZELLE), no en la del comprobante:
    un solo pago en bolívares puede cubrir dos tratos en Zelle a la vez.
    """
    operation_uuid: UUID
    settled_amount: float = Field(..., gt=0)


class OutgoingSettlementsUpdate(BaseModel):
    """Reparto completo del comprobante de salida: reemplaza el anterior y no puede ir vacío."""
    settlements: List[OutgoingSettlementItem]


class OrphanDecision(BaseModel):
    """
    Qué hacer con la operación si al desvincular este pago se queda sin ningún comprobante.
    Sin decisión el backend rechaza el desvinculado (409 `operation_would_be_orphan`).

    - DELETE_OPERATION: borra la op con su transacción y sus movimientos de fondo.
    - KEEP: la conserva y firma quién aceptó dejarla sin pago asociado.
    """
    orphan_action: Optional[Literal["KEEP", "DELETE_OPERATION"]] = None
    orphan_note: Optional[str] = None


class WhatsAppPaymentLink(OrphanDecision):
    operation_uuid: Optional[UUID] = None
    # Solo salientes: cuánto del valor de la operación cubre este comprobante. Sin esto se
    # toma lo que da la tasa de referencia.
    settled_amount: Optional[float] = Field(None, gt=0)


class WhatsAppPersonalExpense(OrphanDecision):
    is_personal_expense: bool
    personal_description: Optional[str] = None


class WhatsAppIrrelevant(OrphanDecision):
    is_irrelevant: bool
    irrelevant_description: Optional[str] = None


class ClientLoanCreate(BaseModel):
    """Registra un pago saliente como préstamo conservando todas sus equivalencias."""
    preferred_value: str = Field(..., description="FIAT | USDT | BCV")
    payment_currency: Optional[str] = Field(None, min_length=2, max_length=10)
    fiat_currency: str = Field(..., min_length=2, max_length=10)
    fiat_amount: Optional[float] = Field(None, gt=0)
    usdt_amount: Optional[float] = Field(None, gt=0)
    bcv_amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None
    # Deudor explícito. Obligatorio cuando el comprobante se mandó a un grupo.
    client_uuid: Optional[UUID] = None

    @validator("preferred_value")
    def validate_preferred_value(cls, value: str) -> str:
        value = value.upper()
        if value not in {"FIAT", "USDT", "BCV"}:
            raise ValueError("preferred_value must be FIAT, USDT or BCV")
        return value

    @validator("fiat_currency")
    def normalize_fiat_currency(cls, value: str) -> str:
        return value.strip().upper()

    @validator("payment_currency")
    def normalize_payment_currency(cls, value: Optional[str]) -> Optional[str]:
        return value.strip().upper() if value else None


class ClientLoanManualCreate(BaseModel):
    """Préstamo dado de alta a mano, sin comprobante que lo respalde."""
    preferred_value: str = Field(..., description="FIAT | USDT | BCV")
    fiat_currency: str = Field(..., min_length=2, max_length=10)
    fiat_amount: float = Field(..., gt=0)
    valuation_at: datetime
    usdt_amount: Optional[float] = Field(None, gt=0)
    bcv_amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None

    @validator("preferred_value")
    def validate_preferred_value(cls, value: str) -> str:
        value = value.upper()
        if value not in {"FIAT", "USDT", "BCV"}:
            raise ValueError("preferred_value must be FIAT, USDT or BCV")
        return value

    @validator("fiat_currency")
    def normalize_fiat_currency(cls, value: str) -> str:
        return value.strip().upper()


class ClientLoanRepaymentCreate(BaseModel):
    """Abono expresado en la referencia preferida del préstamo."""
    preferred_amount: float = Field(..., gt=0)
    notes: Optional[str] = None


class OperationCoveragePayment(BaseModel):
    payment_id: int
    #: Sólo cuando ese comprobante abarca DOS tratos; si no, aporta su monto completo y no hay
    #: nada que teclear.
    settled_amount: Optional[float] = Field(None, gt=0)


class OperationCoverageUncovered(BaseModel):
    """La parte del valor que no tiene comprobante, con el motivo que la deja cerrar."""

    amount: float = Field(..., ge=0)
    reason: Optional[Literal["CASH", "OTHER_CHANNEL", "BALANCE", "ADJUSTMENT"]] = None


class OperationCoverageUpdate(BaseModel):
    """
    Con qué comprobantes se cubre la operación. Es el conjunto COMPLETO, no deltas.

    `partial` distingue «guardo lo que llevo» de «esto ya está cuadrado»: la tasa se deriva de
    la suma sólo al cuadrar, porque a medias la suma está incompleta.
    """

    payments: List[OperationCoveragePayment]
    value_amount: Optional[float] = Field(None, gt=0)
    uncovered: Optional[OperationCoverageUncovered] = None
    partial: bool = False


class WhatsAppForwardToGroup(BaseModel):
    """
    Marcar un pago entrante como contabilizado en un fondo (escenario ZELLE_DIRECT).

    El canal del fondo es su grupo (`group_jid`) o el chat directo con su gestor
    (`manager_phone`): no todo fondo se lleva en un grupo.
    """
    group_jid: Optional[str] = None
    group_uuid: Optional[UUID] = None
    manager_phone: Optional[str] = None


class WhatsAppPaymentTransferRequest(BaseModel):
    """
    Mudar un comprobante a otro cliente. El motivo es obligatorio: es lo que queda en el
    rastro, y sin él la mudanza es indistinguible de un error.
    """
    client_uuid: UUID = Field(..., description="Cliente destino")
    reason: str = Field(..., description="THIRD_PARTY | BOT_MISMATCH | DUPLICATE_CLIENT")
    note: Optional[str] = None


class WhatsAppBalanceCredit(BaseModel):
    """Acreditar un pago entrante como saldo a favor. Sin amount → usa el del pago."""
    amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None


class WhatsAppBalanceDebit(BaseModel):
    """Debitar saldo por una operación de abono. Sin amount → from_amount de la op."""
    amount: Optional[float] = Field(None, gt=0)
    notes: Optional[str] = None


class WhatsAppBalanceAdjust(BaseModel):
    """Ajuste manual de saldo (crédito o débito) desde el front."""
    entry_type: str = Field(..., description="CREDIT | DEBIT")
    amount: float = Field(..., gt=0)
    notes: Optional[str] = None

    @validator('entry_type')
    def validate_entry_type(cls, v: str) -> str:
        v_up = v.upper()
        if v_up not in {"CREDIT", "DEBIT"}:
            raise ValueError("entry_type must be CREDIT or DEBIT")
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
    # Por qué el valor de la operación no es el del comprobante: cuando el operador decide
    # dejar la diferencia (y con ella una tasa efectiva distinta a la cotizada), la decisión
    # se guarda con la operación en vez de perderse en el diálogo que la preguntó.
    notes: Optional[str] = Field(None, max_length=2000)

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


# ===== Reparto del margen de una operación =====

class ProfitAllocationItem(BaseModel):
    """Un destino del margen: un fondo o el propio cliente, con su porcentaje."""
    destination_type: str = Field(..., pattern="^(FUND|CLIENT|fund|client)$")
    fund_group_uuid: Optional[UUID] = None
    client_uuid: Optional[UUID] = None
    percentage: float = Field(..., gt=0, le=99)
    notes: Optional[str] = None

    @validator("fund_group_uuid", always=True)
    def _fund_needs_group(cls, v, values):
        if values.get("destination_type", "").upper() == "FUND" and v is None:
            raise ValueError("Un destino de tipo FUND necesita fund_group_uuid")
        return v

    @validator("client_uuid", always=True)
    def _client_needs_client(cls, v, values):
        if values.get("destination_type", "").upper() == "CLIENT" and v is None:
            raise ValueError("Un destino de tipo CLIENT necesita client_uuid")
        return v


class ProfitAllocationResponse(BaseModel):
    uuid: UUID
    destination_type: str
    fund_group_uuid: Optional[UUID] = None
    client_uuid: Optional[UUID] = None
    destination_name: Optional[str] = None
    percentage: float
    amount_usdt: Optional[float] = None
    approved_by_uuid: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class ProfitAllocationUpdate(BaseModel):
    allocations: List[ProfitAllocationItem]


class ProfitAllocationList(BaseModel):
    """El reparto de una operación, con lo cobrado como referencia."""
    operation_uuid: UUID
    charged_percentage: Optional[float] = None   # lo que se le cobró al cliente
    allocated_percentage: float                  # la suma de los destinos
    unallocated_percentage: float                # lo que sobra (negativo: se repartió de más)
    value_usdt: Optional[float] = None
    allocations: List[ProfitAllocationResponse] = []


class EmailVerificationResponse(BaseModel):
    """
    Resultado de verificar un pago entrante contra los correos de los bancos.

    `confirmed` trae el texto listo para que el bot lo pegue en el mensaje al operador;
    `pending` significa que se sigue buscando en segundo plano; `skipped`, que el pago no
    era verificable (sin monto).
    """

    status: str  # "confirmed" | "pending" | "skipped"
    message: Optional[str] = None
