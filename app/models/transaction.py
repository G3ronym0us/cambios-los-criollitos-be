from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.mixins import UUIDMixin
import enum

class TransactionStatus(enum.Enum):
    """Estados de una transacción"""
    PENDING = "pending"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"

class Transaction(UUIDMixin, Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # Usuario que registró la transacción

    # Información de la transacción
    from_currency = Column(String(10), nullable=False)
    to_currency = Column(String(10), nullable=False)
    from_amount = Column(Float, nullable=False)  # Monto enviado
    to_amount = Column(Float, nullable=False)    # Monto recibido
    exchange_rate = Column(Float, nullable=False)

    # Descripción y tipo
    description = Column(Text, nullable=True)
    transaction_type = Column(String(20), default="conversion")  # conversion, manual, exchange

    # Ganancia y distribución
    total_profit_percentage = Column(Float, default=0.0)  # Porcentaje total de ganancia (ej: 10%)
    profit_amount = Column(Float, default=0.0)            # Ganancia calculada en valor absoluto

    # Estado y auditoría
    status = Column(SQLEnum(TransactionStatus), nullable=False, default=TransactionStatus.PENDING)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    # Relaciones
    user = relationship("User", back_populates="transactions")
    profit_splits = relationship("TransactionProfitSplit", back_populates="transaction", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Transaction(id={self.id}, {self.from_amount} {self.from_currency} -> {self.to_amount} {self.to_currency}, profit={self.profit_amount})>"

    def dict(self):
        """Convertir a diccionario para respuestas JSON"""
        return {
            "uuid": self.uuid,
            "user_uuid": self.user.uuid if self.user else None,
            "from_currency": self.from_currency,
            "to_currency": self.to_currency,
            "from_amount": self.from_amount,
            "to_amount": self.to_amount,
            "exchange_rate": self.exchange_rate,
            "description": self.description,
            "transaction_type": self.transaction_type,
            "total_profit_percentage": self.total_profit_percentage,
            "profit_amount": self.profit_amount,
            "status": self.status.value if self.status else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "completed_at": self.completed_at
        }


class TransactionProfitSplit(UUIDMixin, Base):
    """Modelo para dividir ganancias de una transacción entre múltiples usuarios"""
    __tablename__ = "transaction_profit_splits"

    id = Column(Integer, primary_key=True, index=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Distribución de ganancia
    profit_percentage = Column(Float, nullable=False)  # Porcentaje asignado a este usuario (ej: 5%)
    profit_amount = Column(Float, nullable=False)      # Ganancia calculada para este usuario

    # Auditoría
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    transaction = relationship("Transaction", back_populates="profit_splits")
    user = relationship("User")

    def __repr__(self):
        return f"<TransactionProfitSplit(transaction_id={self.transaction_id}, user_id={self.user_id}, profit={self.profit_amount})>"

    def dict(self):
        """Convertir a diccionario para respuestas JSON"""
        return {
            "uuid": self.uuid,
            "transaction_uuid": self.transaction.uuid if self.transaction else None,
            "user_uuid": self.user.uuid if self.user else None,
            "profit_percentage": self.profit_percentage,
            "profit_amount": self.profit_amount,
            "created_at": self.created_at
        }
