from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base

class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    from_currency = Column(String(10), nullable=False)
    to_currency = Column(String(10), nullable=False)
    from_amount = Column(Float, nullable=False)
    to_amount = Column(Float, nullable=False)
    exchange_rate = Column(Float, nullable=False)
    transaction_type = Column(String(20), default="conversion")  # conversion, manual, etc.
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relación con User
    user = relationship("User", back_populates="transactions")

    def __repr__(self):
        return f"<Transaction({self.from_amount} {self.from_currency} -> {self.to_amount} {self.to_currency})>"
