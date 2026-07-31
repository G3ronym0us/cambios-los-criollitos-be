import uuid as _uuid

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppClientAccount(UUIDMixin, Base):
    """
    Cuenta de pago guardada de un cliente, opcionalmente con nombre de beneficiario.

    Reemplaza a las columnas `default_payment_info`/`default_payment_currency` de
    `whatsapp_clients`: un cliente intermediario paga a terceros distintos en cada
    operación, así que la cuenta no puede ser una sola. La fila con `alias=NULL` e
    `is_default=True` es la cuenta que se usa cuando el mensaje no nombra a nadie.
    """
    __tablename__ = "whatsapp_client_accounts"

    id = Column(Integer, primary_key=True, index=True)
    # La migración crea esta columna como varchar(36) (mismo precedente que FundGroup,
    # FundGroupMember, FundMovement, FundPendingDeposit, OperationProfitAllocation y
    # WhatsAppPaymentAllocation), así que acá se pisa el `uuid` de tipo UUID que trae
    # UUIDMixin: si se deja el de la mixin, SQLAlchemy liga el filtro con un cast
    # `::UUID` y Postgres tira `operator does not exist: character varying = uuid`.
    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(_uuid.uuid4()), index=True)
    client_id = Column(
        Integer, ForeignKey("whatsapp_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Nombre tal como se escribió, y su forma normalizada para buscar (minúsculas,
    # sin acentos, espacios colapsados). NULL = cuenta sin nombre.
    alias = Column(String(120), nullable=True)
    alias_normalized = Column(String(120), nullable=True, index=True)

    payment_info = Column(Text, nullable=False)
    currency = Column(String(10), nullable=False)

    is_default = Column(Boolean, nullable=False, server_default="false")
    is_confirmed = Column(Boolean, nullable=False, server_default="false")
    # MESSAGE = la mandó el cliente por texto | RECEIPT = salió de un comprobante
    # saliente ya pagado | MANUAL = la cargó el operador en el panel.
    source = Column(String(10), nullable=False, server_default="MANUAL")

    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    client = relationship("WhatsAppClient", back_populates="accounts")

    __table_args__ = (
        # La misma cuenta no se guarda dos veces para el mismo cliente.
        UniqueConstraint("client_id", "payment_info", name="uq_client_account_payment_info"),
        Index("ix_client_account_lookup", "client_id", "alias_normalized"),
    )

    def __repr__(self):
        return f"<WhatsAppClientAccount(client_id={self.client_id}, alias={self.alias})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "alias": self.alias,
            "payment_info": self.payment_info,
            "currency": self.currency,
            "is_default": self.is_default,
            "is_confirmed": self.is_confirmed,
            "source": self.source,
            "last_used_at": self.last_used_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
