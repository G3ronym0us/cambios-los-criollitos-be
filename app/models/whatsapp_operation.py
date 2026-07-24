import enum
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppOperationStatus(enum.Enum):
    QUOTED = "QUOTED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class WhatsAppAmountSide(enum.Enum):
    SEND = "SEND"        # cliente envía X de from_currency
    RECEIVE = "RECEIVE"  # cliente quiere recibir X de to_currency


class WhatsAppDeliveryStatus(enum.Enum):
    PENDING = "PENDING"
    RECEIVED = "RECEIVED"


class WhatsAppOperationScenario(enum.Enum):
    """
    Comportamiento/escenario de la operación según quién recibe el entrante y a quién
    se le envía el saliente.

    - NORMAL:       cliente <-> operador, sin grupo (default histórico).
    - ZELLE_DIRECT: el cliente envía un Zelle (entrante), yo gestiono el cambio,
                    reenvío el Zelle al grupo (ledger) y pago en Bs al cliente.
    - VIA_PARTNER:  un socio (ej. Jean) recibe el entrante en su WhatsApp y me reporta
                    monto+datos; yo pago en Bs y subo el capture al grupo.
    """
    NORMAL = "NORMAL"
    ZELLE_DIRECT = "ZELLE_DIRECT"
    VIA_PARTNER = "VIA_PARTNER"


class WhatsAppOperation(UUIDMixin, Base):
    """
    Operación originada en WhatsApp. Ciclo: QUOTED -> PENDING -> COMPLETED.
    Si tiene una Transaction vinculada, esta refleja su estado y datos contables.
    """
    __tablename__ = "whatsapp_operations"

    id = Column(Integer, primary_key=True, index=True)

    client_id = Column(Integer, ForeignKey("whatsapp_clients.id"), nullable=False, index=True)
    currency_pair_id = Column(Integer, ForeignKey("currency_pairs.id"), nullable=False, index=True)

    # Cotización congelada en el momento
    # ── Valor del trato ──────────────────────────────────────────────────────
    # Cuánto vale la operación y en qué moneda (lo que entrega el cliente), con sus
    # equivalentes: USDT siempre, y BCV cuando el valor está en bolívares. Es lo que se
    # contabiliza y sobre lo que se calcula la ganancia, independientemente de en cuántas
    # monedas se haya pagado.
    amount = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True)
    amount_usdt = Column(Float, nullable=True)
    usdt_rate = Column(Float, nullable=True)
    bcv_amount = Column(Float, nullable=True)
    bcv_rate = Column(Float, nullable=True)
    valuation_at = Column(DateTime(timezone=True), nullable=True)

    # ── Cotización prometida ─────────────────────────────────────────────────
    # El par, los montos y la tasa que se le dijeron al cliente. Dejan de ser lo que se
    # pagó (eso lo dicen los comprobantes salientes) y quedan como referencia.
    from_amount = Column(Float, nullable=False)
    to_amount = Column(Float, nullable=False)
    rate_used = Column(Float, nullable=False)
    inverse_percentage = Column(Boolean, default=False, nullable=False)
    applied_percentage = Column(Float, nullable=True)   # margen efectivamente aplicado
    default_percentage = Column(Float, nullable=True)   # margen base de la API
    amount_side = Column(SQLEnum(WhatsAppAmountSide), nullable=False)

    # Path BCV: si la cotización se ancló al USD oficial
    bcv_usd = Column(Float, nullable=True)

    status = Column(SQLEnum(WhatsAppOperationStatus), nullable=False, default=WhatsAppOperationStatus.QUOTED, index=True)

    # Escenario/comportamiento de la operación (clasificado por el bot, editable a mano)
    scenario = Column(
        SQLEnum(WhatsAppOperationScenario),
        nullable=False,
        server_default=WhatsAppOperationScenario.NORMAL.value,
        index=True,
    )
    # Grupo contable (FundGroup, ej. "cambios d&j") al que pertenece el cambio
    fund_group_id = Column(Integer, ForeignKey("fund_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    # Quién recibió el pago ENTRANTE. NULL = operador (yo); un socio (Jean) = su User.
    received_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    # Tracking de entrega de USD efectivo (cliente vende USD físico al operador)
    delivery_status = Column(SQLEnum(WhatsAppDeliveryStatus), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Notas: payment_info extraída por el bot (cédula, banco, teléfono, etc.)
    notes = Column(Text, nullable=True)

    # Operación que quedó sin ningún comprobante vinculado y el operador decidió conservar
    # igual (al desvincular su último pago). Queda quién lo aceptó, cuándo y por qué: una op
    # completada sin pago que la respalde no puede pasar en silencio.
    no_payments_ack_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    no_payments_ack_at = Column(DateTime(timezone=True), nullable=True)
    no_payments_ack_note = Column(Text, nullable=True)

    # Vínculo con la Transaction derivada. Puede existir desde QUOTED/PENDING si hay fondo.
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="SET NULL"), nullable=True, index=True)

    # UUID original de la operación cuando vivía en SQLite del bot. Permite reconciliar
    # el dashboard antiguo con el backend durante la coexistencia.
    legacy_sqlite_id = Column(String(36), nullable=True, index=True)

    quoted_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    cancelled_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    client = relationship("WhatsAppClient", back_populates="operations")
    # Los comprobantes de salida dicen cómo se pagó el trato; su `settled_amount` suma lo
    # entregado. Solo lectura: el vínculo lo maneja el servicio de pagos.
    outgoing_payments = relationship(
        "WhatsAppOutgoingPayment",
        primaryjoin="WhatsAppOperation.id == foreign(WhatsAppOutgoingPayment.whatsapp_operation_id)",
        viewonly=True,
    )
    currency_pair = relationship("CurrencyPair", lazy="joined")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])
    fund_group = relationship("FundGroup", foreign_keys=[fund_group_id])
    received_by = relationship("User", foreign_keys=[received_by_user_id])
    no_payments_ack_by = relationship("User", foreign_keys=[no_payments_ack_by_user_id])

    def __repr__(self):
        return (
            f"<WhatsAppOperation(uuid={self.uuid}, status={self.status.value if self.status else None}, "
            f"{self.from_amount}->{self.to_amount} @ {self.rate_used})>"
        )

    @property
    def delivered_amount(self) -> float:
        """Cuánto del valor cubren ya sus comprobantes de salida."""
        return round(
            sum(p.settled_amount or 0 for p in (self.outgoing_payments or [])),
            2,
        )

    def dict(self):
        cp = self.currency_pair
        value = self.amount if self.amount is not None else self.from_amount
        delivered = self.delivered_amount
        return {
            "uuid": self.uuid,
            "client_uuid": self.client.uuid if self.client else None,
            "client_phone": self.client.phone if self.client else None,
            "client_display_name": self.client.display_name if self.client else None,
            "currency_pair_uuid": cp.uuid if cp else None,
            "pair_symbol": cp.pair_symbol if cp else None,
            "from_currency": cp.from_currency.symbol if cp and cp.from_currency else None,
            "to_currency": cp.to_currency.symbol if cp and cp.to_currency else None,
            "amount": self.amount,
            "currency": self.currency,
            "delivered_amount": delivered,
            "pending_amount": round((value or 0) - delivered, 2),
            "amount_usdt": self.amount_usdt,
            "usdt_rate": self.usdt_rate,
            "bcv_amount": self.bcv_amount,
            "bcv_rate": self.bcv_rate,
            "valuation_at": self.valuation_at,
            "from_amount": self.from_amount,
            "to_amount": self.to_amount,
            "rate_used": self.rate_used,
            "inverse_percentage": self.inverse_percentage,
            "applied_percentage": self.applied_percentage,
            "default_percentage": self.default_percentage,
            "amount_side": self.amount_side.value if self.amount_side else None,
            "bcv_usd": self.bcv_usd,
            "status": self.status.value if self.status else None,
            "scenario": self.scenario.value if self.scenario else None,
            "fund_group_uuid": self.fund_group.uuid if self.fund_group else None,
            "fund_group_name": self.fund_group.name if self.fund_group else None,
            "received_by_user_uuid": self.received_by.uuid if self.received_by else None,
            "received_by_username": self.received_by.username if self.received_by else None,
            "delivery_status": self.delivery_status.value if self.delivery_status else None,
            "delivered_at": self.delivered_at,
            "notes": self.notes,
            "no_payments_ack_by_username": (
                self.no_payments_ack_by.username if self.no_payments_ack_by else None
            ),
            "no_payments_ack_at": self.no_payments_ack_at,
            "no_payments_ack_note": self.no_payments_ack_note,
            "transaction_uuid": self.transaction.uuid if self.transaction else None,
            "legacy_sqlite_id": self.legacy_sqlite_id,
            "quoted_at": self.quoted_at,
            "expires_at": self.expires_at,
            "approved_at": self.approved_at,
            "completed_at": self.completed_at,
            "cancelled_at": self.cancelled_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
