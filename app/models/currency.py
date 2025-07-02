from sqlalchemy import Column, Integer, String, DateTime, Enum as SQLEnum
from sqlalchemy.sql import func
from app.database.connection import Base
from enum import Enum

class CurrencyType(Enum):
    FIAT = "FIAT"
    CRYPTO = "CRYPTO"
    COMMODITY = "COMMODITY"

class Currency(Base):
    __tablename__ = "currencies"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)  # Full name (e.g., "US Dollar")
    symbol = Column(String(10), unique=True, index=True, nullable=False)  # Symbol/code (e.g., "USD")
    description = Column(String, nullable=True)  # Optional description
    currency_type = Column(SQLEnum(CurrencyType), nullable=False, default=CurrencyType.FIAT)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<Currency(id={self.id}, symbol={self.symbol}, name={self.name}, type={self.currency_type.value})>"

    def dict(self):
        """Convert to dictionary for JSON responses"""
        return {
            "id": self.id,
            "name": self.name,
            "symbol": self.symbol,
            "description": self.description,
            "currency_type": self.currency_type.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }