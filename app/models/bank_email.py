"""
Confirmación de Zelle contra los correos que mandan los bancos de las cuentas alquiladas.

Dos tablas con papeles distintos:

- `bank_email_notifications`: qué correos llegaron. Las llena el poller; no sabe nada de
  operaciones. `message_id` único hace la ingesta idempotente y `consumed_by_payment_id`
  impide que dos Zelle del mismo monto se confirmen con el mismo correo.
- `bank_email_verifications`: qué pagos estamos esperando confirmar. Una por pago
  entrante reenviado al grupo.
"""

import enum

from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, Numeric, String, Text, Enum as SAEnum,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class BankEmailVerificationStatus(str, enum.Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    NOT_FOUND = "NOT_FOUND"


class BankEmailNotification(UUIDMixin, Base):
    __tablename__ = "bank_email_notifications"

    id = Column(Integer, primary_key=True, index=True)
    #: Message-ID del correo. Único: es lo que hace la ingesta idempotente.
    message_id = Column(String(255), nullable=False, unique=True, index=True)
    #: Etiqueta legible del buzón ("Jean", "Mariana"): es lo que sale en el aviso.
    mailbox_label = Column(String(60), nullable=False)
    mailbox_email = Column(String(255), nullable=False)
    bank = Column(String(40), nullable=False)
    sender_name = Column(String(200), nullable=True)
    amount = Column(Numeric(12, 2), nullable=False, index=True)
    currency = Column(String(10), nullable=False, default="USD")
    received_at = Column(DateTime(timezone=True), nullable=False, index=True)
    subject = Column(Text, nullable=True)
    #: Cabecera Authentication-Results tal cual, para poder auditar después.
    auth_result = Column(Text, nullable=True)
    consumed_by_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class BankEmailVerification(UUIDMixin, Base):
    __tablename__ = "bank_email_verifications"

    id = Column(Integer, primary_key=True, index=True)
    incoming_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="CASCADE"),
        nullable=False, unique=True, index=True,
    )
    amount = Column(Numeric(12, 2), nullable=False)
    status = Column(
        SAEnum(BankEmailVerificationStatus, name="bank_email_verification_status"),
        nullable=False, default=BankEmailVerificationStatus.PENDING, index=True,
    )
    matched_notification_id = Column(
        Integer, ForeignKey("bank_email_notifications.id", ondelete="SET NULL"), nullable=True,
    )
    requested_at = Column(DateTime(timezone=True), nullable=False)
    #: Índice 0-based dentro de ESCALATION_MINUTES: qué aviso toca mandar.
    escalation_step = Column(Integer, nullable=False, default=0)
    next_notify_at = Column(DateTime(timezone=True), nullable=True, index=True)
    #: Si el buzón no se pudo leer, la escalera se congela hasta acá: el sistema nunca
    #: declara "no confirmado" cuando en realidad no pudo mirar.
    frozen_until = Column(DateTime(timezone=True), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    notification = relationship("BankEmailNotification", foreign_keys=[matched_notification_id])
