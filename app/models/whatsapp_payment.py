"""
Pagos originados/extraídos en WhatsApp (comprobantes OCR). Espejo de las tablas
SQLite `incoming_payments` / `outgoing_payments` del bot.

- `client_phone` es texto libre: puede ser un teléfono o un JID de grupo (`...@g.us`)
  cuando el comprobante se reenvía al grupo de una cuenta alquilada.
- El vínculo con una operación es por `whatsapp_operation_id` (FK interno); la API
  expone/acepta el `operation_uuid`.
- Los flags se exponen como 0/1 (espejo del SQLite del bot) para minimizar el mapeo
  en el bridge.
"""

from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppIncomingPayment(UUIDMixin, Base):
    __tablename__ = "whatsapp_incoming_payments"

    id = Column(Integer, primary_key=True, index=True)
    client_phone = Column(String(64), nullable=False, index=True)
    provider = Column(String(60), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True)
    bank_from = Column(String(120), nullable=True)
    bank_to = Column(String(120), nullable=True)
    account_number = Column(String(60), nullable=True)
    identification = Column(String(60), nullable=True)
    phone_to = Column(String(40), nullable=True)
    reference = Column(String(120), nullable=True)
    raw_text = Column(Text, nullable=True)
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    correction_original = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])

    def dict(self):
        return {
            "id": self.id,
            "uuid": self.uuid,
            "client_phone": self.client_phone,
            "provider": self.provider,
            "amount": self.amount,
            "currency": self.currency,
            "bank_from": self.bank_from,
            "bank_to": self.bank_to,
            "account_number": self.account_number,
            "identification": self.identification,
            "phone_to": self.phone_to,
            "reference": self.reference,
            "raw_text": self.raw_text,
            "operation_uuid": self.operation.uuid if self.operation else None,
            "corrected_at": self.corrected_at,
            "correction_original": self.correction_original,
            "created_at": self.created_at,
        }


class WhatsAppOutgoingPayment(UUIDMixin, Base):
    __tablename__ = "whatsapp_outgoing_payments"

    id = Column(Integer, primary_key=True, index=True)
    client_phone = Column(String(64), nullable=False, index=True)
    provider = Column(String(60), nullable=True)
    amount = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True)
    bank_from = Column(String(120), nullable=True)
    bank_to = Column(String(120), nullable=True)
    account_number = Column(String(60), nullable=True)
    identification = Column(String(60), nullable=True)
    phone_to = Column(String(40), nullable=True)
    reference = Column(String(120), nullable=True)
    raw_text = Column(Text, nullable=True)
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    is_personal_expense = Column(Boolean, nullable=False, server_default="false")
    personal_description = Column(Text, nullable=True)
    is_irrelevant = Column(Boolean, nullable=False, server_default="false")
    irrelevant_description = Column(Text, nullable=True)
    # Cadena de reenvío: comprobante Zelle entrante reenviado al grupo de la cuenta alquilada.
    source_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    corrected_at = Column(DateTime(timezone=True), nullable=True)
    correction_original = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])

    def dict(self):
        return {
            "id": self.id,
            "uuid": self.uuid,
            "client_phone": self.client_phone,
            "provider": self.provider,
            "amount": self.amount,
            "currency": self.currency,
            "bank_from": self.bank_from,
            "bank_to": self.bank_to,
            "account_number": self.account_number,
            "identification": self.identification,
            "phone_to": self.phone_to,
            "reference": self.reference,
            "raw_text": self.raw_text,
            "operation_uuid": self.operation.uuid if self.operation else None,
            "is_personal_expense": 1 if self.is_personal_expense else 0,
            "personal_description": self.personal_description,
            "is_irrelevant": 1 if self.is_irrelevant else 0,
            "irrelevant_description": self.irrelevant_description,
            "source_payment_id": self.source_payment_id,
            "corrected_at": self.corrected_at,
            "correction_original": self.correction_original,
            "created_at": self.created_at,
        }
