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


class WhatsAppOperation(UUIDMixin, Base):
    """
    Operación originada en WhatsApp. Ciclo: QUOTED -> PENDING -> COMPLETED.
    Al COMPLETED se crea/vincula un Transaction (profit splits, fondos).
    """
    __tablename__ = "whatsapp_operations"

    id = Column(Integer, primary_key=True, index=True)

    client_id = Column(Integer, ForeignKey("whatsapp_clients.id"), nullable=False, index=True)
    currency_pair_id = Column(Integer, ForeignKey("currency_pairs.id"), nullable=False, index=True)

    # Cotización congelada en el momento
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

    # Tracking de entrega de USD efectivo (cliente vende USD físico al operador)
    delivery_status = Column(SQLEnum(WhatsAppDeliveryStatus), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Notas: payment_info extraída por el bot (cédula, banco, teléfono, etc.)
    notes = Column(Text, nullable=True)

    # Vínculo con la Transaction generada al completar (None mientras QUOTED/PENDING)
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
    currency_pair = relationship("CurrencyPair", lazy="joined")
    transaction = relationship("Transaction", foreign_keys=[transaction_id])

    def __repr__(self):
        return (
            f"<WhatsAppOperation(uuid={self.uuid}, status={self.status.value if self.status else None}, "
            f"{self.from_amount}->{self.to_amount} @ {self.rate_used})>"
        )

    def dict(self):
        cp = self.currency_pair
        return {
            "uuid": self.uuid,
            "client_uuid": self.client.uuid if self.client else None,
            "client_phone": self.client.phone if self.client else None,
            "client_display_name": self.client.display_name if self.client else None,
            "currency_pair_uuid": cp.uuid if cp else None,
            "pair_symbol": cp.pair_symbol if cp else None,
            "from_currency": cp.from_currency.symbol if cp and cp.from_currency else None,
            "to_currency": cp.to_currency.symbol if cp and cp.to_currency else None,
            "from_amount": self.from_amount,
            "to_amount": self.to_amount,
            "rate_used": self.rate_used,
            "inverse_percentage": self.inverse_percentage,
            "applied_percentage": self.applied_percentage,
            "default_percentage": self.default_percentage,
            "amount_side": self.amount_side.value if self.amount_side else None,
            "bcv_usd": self.bcv_usd,
            "status": self.status.value if self.status else None,
            "delivery_status": self.delivery_status.value if self.delivery_status else None,
            "delivered_at": self.delivered_at,
            "notes": self.notes,
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
