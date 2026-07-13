"""Préstamos concedidos a clientes desde pagos salientes.

Cada préstamo conserva las tres referencias disponibles al originarse:
fiat, USDT y BCV (esta última solo cuando la fiat es VES). La referencia
preferida define la unidad en la que se lleva el saldo pendiente.
"""

import enum

from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class ClientLoanPreferredValue(enum.Enum):
    FIAT = "FIAT"
    USDT = "USDT"
    BCV = "BCV"


class ClientLoanStatus(enum.Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    PAID = "PAID"
    CANCELLED = "CANCELLED"


class ClientLoan(UUIDMixin, Base):
    __tablename__ = "client_loans"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(Integer, ForeignKey("whatsapp_clients.id"), nullable=False, index=True)
    outgoing_payment_id = Column(
        Integer,
        ForeignKey("whatsapp_outgoing_payments.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )

    fiat_amount = Column(Numeric(24, 8), nullable=False)
    fiat_currency = Column(String(10), nullable=False)
    usdt_amount = Column(Numeric(24, 8), nullable=False)
    # Unidades fiat por 1 USDT al crear el préstamo.
    usdt_rate = Column(Numeric(24, 8), nullable=False)
    # Equivalente en USD BCV; solo existe para fiat VES.
    bcv_amount = Column(Numeric(24, 8), nullable=True)
    bcv_rate = Column(Numeric(24, 8), nullable=True)

    preferred_value = Column(SQLEnum(ClientLoanPreferredValue), nullable=False)
    status = Column(
        SQLEnum(ClientLoanStatus), nullable=False, default=ClientLoanStatus.OPEN,
        server_default=ClientLoanStatus.OPEN.value,
    )
    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    client = relationship("WhatsAppClient", foreign_keys=[client_id])
    outgoing_payment = relationship("WhatsAppOutgoingPayment", foreign_keys=[outgoing_payment_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    repayments = relationship(
        "ClientLoanRepayment",
        back_populates="loan",
        cascade="all, delete-orphan",
        order_by="ClientLoanRepayment.created_at",
    )

    @property
    def preferred_principal(self) -> float:
        if self.preferred_value == ClientLoanPreferredValue.USDT:
            return float(self.usdt_amount)
        if self.preferred_value == ClientLoanPreferredValue.BCV:
            return float(self.bcv_amount or 0)
        return float(self.fiat_amount)

    @property
    def preferred_currency(self) -> str:
        if self.preferred_value == ClientLoanPreferredValue.USDT:
            return "USDT"
        if self.preferred_value == ClientLoanPreferredValue.BCV:
            return "USD_BCV"
        return self.fiat_currency

    @property
    def outstanding_amount(self) -> float:
        paid = sum(float(entry.preferred_amount) for entry in self.repayments)
        return max(round(self.preferred_principal - paid, 8), 0.0)

    def payment_summary(self) -> dict:
        return {
            "uuid": self.uuid,
            "status": self.status.value,
            "preferred_value": self.preferred_value.value,
            "preferred_currency": self.preferred_currency,
            "principal_amount": self.preferred_principal,
            "outstanding_amount": self.outstanding_amount,
        }


class ClientLoanRepayment(UUIDMixin, Base):
    __tablename__ = "client_loan_repayments"

    id = Column(Integer, primary_key=True, index=True)
    loan_id = Column(Integer, ForeignKey("client_loans.id", ondelete="CASCADE"), nullable=False, index=True)

    # Monto que reduce la deuda, expresado en la referencia preferida del préstamo.
    preferred_amount = Column(Numeric(24, 8), nullable=False)
    fiat_amount = Column(Numeric(24, 8), nullable=False)
    fiat_currency = Column(String(10), nullable=False)
    usdt_amount = Column(Numeric(24, 8), nullable=False)
    usdt_rate = Column(Numeric(24, 8), nullable=False)
    bcv_amount = Column(Numeric(24, 8), nullable=True)
    bcv_rate = Column(Numeric(24, 8), nullable=True)

    notes = Column(Text, nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    loan = relationship("ClientLoan", back_populates="repayments")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def dict(self) -> dict:
        return {
            "uuid": self.uuid,
            "preferred_amount": float(self.preferred_amount),
            "fiat_amount": float(self.fiat_amount),
            "fiat_currency": self.fiat_currency,
            "usdt_amount": float(self.usdt_amount),
            "usdt_rate": float(self.usdt_rate),
            "bcv_amount": float(self.bcv_amount) if self.bcv_amount is not None else None,
            "bcv_rate": float(self.bcv_rate) if self.bcv_rate is not None else None,
            "notes": self.notes,
            "created_by_username": self.created_by.username if self.created_by else None,
            "created_at": self.created_at,
        }
