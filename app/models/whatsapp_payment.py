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

from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, ForeignKey, Text, UniqueConstraint,
)
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
    # Reparto del pago entre operaciones (un Zelle puede cubrir varios cambios). El FK de
    # arriba es la op principal — la de la asignación mayor — y se mantiene por compatibilidad
    # con el bot y el matcher.
    allocations = relationship(
        "WhatsAppPaymentAllocation",
        back_populates="payment",
        cascade="all, delete-orphan",
        order_by="WhatsAppPaymentAllocation.id",
    )
    # Grupo del que vino el comprobante cuando `client_phone` es el JID del grupo (el operador
    # lo reenvió ahí y el pago se movió a la bandeja de entrantes). Solo lectura, por JID.
    fund_group_by_jid = relationship(
        "FundGroup",
        primaryjoin="foreign(WhatsAppIncomingPayment.client_phone) == FundGroup.whatsapp_group_jid",
        viewonly=True,
        uselist=False,
    )

    @property
    def fund_group(self):
        """
        Fondo contable donde cae este entrante. Se DERIVA — antes era la columna
        `fund_group_id`, el único enlace directo pago→fondo del modelo (el saliente no lo
        tenía) y por tanto redundante: el fondo llega por la operación. Si el pago aún no
        tiene op, vale el grupo del que vino el comprobante (client_phone = JID del grupo).
        """
        op = self.operation
        if op is not None and op.fund_group is not None:
            return op.fund_group
        return self.fund_group_by_jid

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
            "fund_group_uuid": self.fund_group.uuid if self.fund_group else None,
            "fund_group_name": self.fund_group.name if self.fund_group else None,
            "corrected_at": self.corrected_at,
            "correction_original": self.correction_original,
            "created_at": self.created_at,
        }


class WhatsAppPaymentAllocation(UUIDMixin, Base):
    """
    Qué parte de un pago ENTRANTE le corresponde a cada operación. Un Zelle de 220 puede
    cubrir dos cambios (200 a BRL y 20 a VES): el FK único del pago solo alcanzaba para uno
    y el otro quedaba sin comprobante.

    Es documental, no contable: cada operación sigue teniendo su propia tasa, su transacción
    y su movimiento de fondo. Aquí solo se dice de dónde salió su dinero. Los montos van en la
    moneda del pago (el lado que envió el cliente); la suma nunca puede pasarse del pago.
    """
    __tablename__ = "whatsapp_payment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "incoming_payment_id", "whatsapp_operation_id", name="uq_allocation_payment_operation"
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    incoming_payment_id = Column(
        Integer, ForeignKey("whatsapp_incoming_payments.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    amount = Column(Float, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    payment = relationship("WhatsAppIncomingPayment", back_populates="allocations")
    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def dict(self):
        op = self.operation
        cp = op.currency_pair if op else None
        return {
            "uuid": self.uuid,
            "amount": self.amount,
            "operation_uuid": op.uuid if op else None,
            "operation_status": op.status.value if op and op.status else None,
            "pair_symbol": cp.pair_symbol if cp else None,
            "from_amount": op.from_amount if op else None,
            "from_currency": cp.from_currency.symbol if cp and cp.from_currency else None,
            "to_amount": op.to_amount if op else None,
            "to_currency": cp.to_currency.symbol if cp and cp.to_currency else None,
            "rate_used": op.rate_used if op else None,
            "created_by_username": self.created_by.username if self.created_by else None,
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
    # Cuánto del valor de su operación cubre este comprobante, en la moneda del valor: el Pix
    # de 914,04 BRL cubre 200 de un trato de 220 ZELLE. NULL = todavía no se ha dicho.
    settled_amount = Column(Float, nullable=True)
    # Tasa contra la que se comparó al vincular (la cotizada, o la activa del par), para que la
    # diferencia entre lo pagado y lo que tocaba siga siendo auditable si el par cambia.
    settled_reference_rate = Column(Float, nullable=True)
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
            "settled_amount": self.settled_amount,
            "settled_reference_rate": self.settled_reference_rate,
            # Tasa a la que realmente se pagó esta parte del trato.
            "settled_rate": (
                round(self.amount / self.settled_amount, 6)
                if self.amount and self.settled_amount else None
            ),
            "is_personal_expense": 1 if self.is_personal_expense else 0,
            "personal_description": self.personal_description,
            "is_irrelevant": 1 if self.is_irrelevant else 0,
            "irrelevant_description": self.irrelevant_description,
            "source_payment_id": self.source_payment_id,
            "corrected_at": self.corrected_at,
            "correction_original": self.correction_original,
            "created_at": self.created_at,
        }
