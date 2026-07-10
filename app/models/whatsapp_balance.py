"""
Saldo a favor del cliente (ledger). Cada fila es un movimiento inmutable:

- CREDIT: el cliente dejó plata "en cuenta" (típicamente un Zelle entrante que se
  pagará en varios abonos). Se vincula al pago entrante que lo originó.
- DEBIT: un abono/pago parcial contra ese saldo. Se vincula a la operación de
  cambio (cotizada a la tasa del día del abono) que lo consumió.

El saldo del cliente = SUM(CREDIT) - SUM(DEBIT). Se lleva en la moneda de
liquidación (USD): un Zelle de 200 acredita 200 USD; un abono de 30 USD (pagado
en Bs a la tasa del día) debita 30.
"""

import enum

from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppBalanceEntryType(enum.Enum):
    CREDIT = "CREDIT"
    DEBIT = "DEBIT"


class WhatsAppBalanceEntry(UUIDMixin, Base):
    __tablename__ = "whatsapp_balance_entries"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("whatsapp_clients.id"), nullable=False, index=True)
    entry_type = Column(SQLEnum(WhatsAppBalanceEntryType), nullable=False)
    # Siempre > 0; el signo lo da entry_type.
    amount = Column(Float, nullable=False)
    currency = Column(String(10), nullable=False, server_default="USD")

    # Origen del CREDIT: el pago entrante (ej. Zelle 200) que dejó el saldo.
    incoming_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Origen del DEBIT: la operación (abono a tasa del día) que consumió saldo.
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="SET NULL"), nullable=True, index=True
    )

    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    client = relationship("WhatsAppClient", foreign_keys=[client_id])
    incoming_payment = relationship("WhatsAppIncomingPayment", foreign_keys=[incoming_payment_id])
    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return (
            f"<WhatsAppBalanceEntry(client_id={self.client_id}, "
            f"{self.entry_type.value if self.entry_type else None} {self.amount} {self.currency})>"
        )

    def dict(self):
        op = self.operation
        return {
            "uuid": self.uuid,
            "client_uuid": self.client.uuid if self.client else None,
            "entry_type": self.entry_type.value if self.entry_type else None,
            "amount": self.amount,
            "currency": self.currency,
            "incoming_payment_id": self.incoming_payment_id,
            "operation_uuid": op.uuid if op else None,
            "operation_rate_used": op.rate_used if op else None,
            "operation_to_amount": op.to_amount if op else None,
            "notes": self.notes,
            "created_by_username": self.created_by.username if self.created_by else None,
            "created_at": self.created_at,
        }
