from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, UniqueConstraint, Numeric, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.currency import CurrencyType

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
    binance_tracked = Column(Boolean, default=False, nullable=False)  # Indica si el par se busca en Binance
    
    # Binance tracking specific fields (only required when binance_tracked=True)
    banks_to_track = Column(JSON, nullable=True)  # Array of bank names to track in Binance
    amount_to_track = Column(Numeric(15, 2), nullable=True)  # Specific amount to track
    
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
            "binance_tracked": self.binance_tracked,
            "banks_to_track": self.banks_to_track,
            "amount_to_track": float(self.amount_to_track) if self.amount_to_track else None,
            "description": self.description,
            "created_at": self.created_at,
            "updated_at": self.updated_at
        }

    @classmethod
    def create_pair_symbol(cls, from_symbol: str, to_symbol: str) -> str:
        """Create standardized pair symbol"""
        return f"{from_symbol.upper()}-{to_symbol.upper()}"

    def validate_binance_tracking(self) -> tuple[bool, str]:
        """Validate binance tracking requirements"""
        if not self.binance_tracked:
            return True, ""
        
        # Check if one currency is FIAT and the other is CRYPTO
        if not self.from_currency or not self.to_currency:
            return False, "Currency information not loaded"
        
        from_type = self.from_currency.currency_type
        to_type = self.to_currency.currency_type
        
        valid_combination = (
            (from_type == CurrencyType.FIAT and to_type == CurrencyType.CRYPTO) or
            (from_type == CurrencyType.CRYPTO and to_type == CurrencyType.FIAT)
        )
        
        if not valid_combination:
            return False, "Binance tracked pairs must be between FIAT and CRYPTO currencies"
        
        # Check required fields
        if not self.banks_to_track or len(self.banks_to_track) == 0:
            return False, "banks_to_track is required when binance_tracked is True"
        
        if not self.amount_to_track or self.amount_to_track <= 0:
            return False, "amount_to_track is required and must be greater than 0 when binance_tracked is True"
        
        return True, ""

    async def validate_binance_tracking_with_api(self) -> tuple[bool, str, dict]:
        """
        Validate binance tracking requirements including real-time API validation
        
        Returns:
            Tuple of (is_valid, error_message, validation_data)
        """
        # First run basic validation
        basic_valid, basic_error = self.validate_binance_tracking()
        if not basic_valid:
            return False, basic_error, {}
        
        # If basic validation passes, validate with Binance API
        from app.services.binance_validation_service import BinanceValidationService
        
        try:
            is_valid, message, validation_data = await BinanceValidationService.validate_currency_pair_configuration(
                from_currency=self.from_currency.symbol,
                to_currency=self.to_currency.symbol,
                from_currency_type=self.from_currency.currency_type,
                to_currency_type=self.to_currency.currency_type,
                banks_to_track=self.banks_to_track,
                amount_to_track=self.amount_to_track
            )
            
            return is_valid, message, validation_data or {}
            
        except Exception as e:
            return False, f"Error validating with Binance API: {str(e)}", {}