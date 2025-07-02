from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base

class CurrencyPair(Base):
    __tablename__ = "currency_pairs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign keys to currencies table
    from_currency_id = Column(Integer, ForeignKey("currencies.id"), nullable=False)
    to_currency_id = Column(Integer, ForeignKey("currencies.id"), nullable=False)
    
    # Pair identifier (e.g., "USDT-VES", "ZELLE-COP")
    pair_symbol = Column(String(20), unique=True, index=True, nullable=False)
    
    # Configuration
    is_active = Column(Boolean, default=True, nullable=False)
    is_monitored = Column(Boolean, default=True, nullable=False)  # Para scraping automático
    
    # Optional description
    description = Column(String, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    from_currency = relationship("Currency", foreign_keys=[from_currency_id])
    to_currency = relationship("Currency", foreign_keys=[to_currency_id])
    
    # Ensure unique pair combination
    __table_args__ = (
        UniqueConstraint('from_currency_id', 'to_currency_id', name='unique_currency_pair'),
    )

    def __repr__(self):
        return f"<CurrencyPair(id={self.id}, pair={self.pair_symbol}, active={self.is_active})>"

    @property
    def display_name(self) -> str:
        """Display name for the pair"""
        if self.from_currency and self.to_currency:
            return f"{self.from_currency.symbol}/{self.to_currency.symbol}"
        return self.pair_symbol

    @property
    def reverse_pair_symbol(self) -> str:
        """Get the reverse pair symbol (e.g., VES-USDT from USDT-VES)"""
        if self.from_currency and self.to_currency:
            return f"{self.to_currency.symbol}-{self.from_currency.symbol}"
        return ""

    def dict(self):
        """Convert to dictionary for JSON responses"""
        return {
            "id": self.id,
            "pair_symbol": self.pair_symbol,
            "from_currency_id": self.from_currency_id,
            "to_currency_id": self.to_currency_id,
            "from_currency": self.from_currency.dict() if self.from_currency else None,
            "to_currency": self.to_currency.dict() if self.to_currency else None,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "is_monitored": self.is_monitored,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def create_pair_symbol(cls, from_symbol: str, to_symbol: str) -> str:
        """Create standardized pair symbol"""
        return f"{from_symbol.upper()}-{to_symbol.upper()}"