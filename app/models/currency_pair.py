from sqlalchemy import Column, Integer, String, DateTime, Boolean, Float, ForeignKey, UniqueConstraint, Numeric, JSON, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.currency import CurrencyType
from app.enums.pair_type import PairType
from app.models.mixins import UUIDMixin

class CurrencyPair(UUIDMixin, Base):
    __tablename__ = "currency_pairs"

    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign keys to currencies table
    from_currency_id = Column(Integer, ForeignKey("currencies.id"), nullable=False)
    to_currency_id = Column(Integer, ForeignKey("currencies.id"), nullable=False)
    
    # Base pair for derived rates (nullable for primary pairs)
    base_pair_id = Column(Integer, ForeignKey("currency_pairs.id"), nullable=True)

    # USDT conversion config — used when auto-creating FundMovements from transactions
    usdt_reference_side = Column(String(4), nullable=True)   # "FROM" or "TO"
    usdt_manual_rate    = Column(Float, nullable=True)        # reference_amount * rate = amount_usdt
    usdt_pair_id        = Column(Integer, ForeignKey("currency_pairs.id"), nullable=True)
    usdt_pair_inverse   = Column(Boolean, default=False, nullable=False)
    
    # Derived rate configuration (only used when base_pair_id is set)
    derived_percentage = Column(Numeric(5, 2), nullable=True)  # Percentage to apply (e.g., 5.00 for 5%)
    use_inverse_percentage = Column(Boolean, default=False, nullable=False)  # Apply percentage inversely

    # Quote rounding configuration (applied when creating WhatsApp quotes).
    #   rounding_mode:        None (off) | "RATE" (round the per-unit rate) | "AMOUNT" (round a side's amount)
    #   rounding_step:        the multiple to round to (e.g. 100, 5)
    #   rounding_direction:   "UP" | "DOWN"
    #   rounding_amount_side: "FROM" | "TO" — only for AMOUNT mode: which side's amount is rounded,
    #                         and only when that side is the *calculated* one (not the client's input).
    rounding_mode = Column(String(6), nullable=True)
    rounding_step = Column(Numeric(15, 4), nullable=True)
    rounding_direction = Column(String(4), nullable=True)
    rounding_amount_side = Column(String(4), nullable=True)
    
    # Pair identifier (e.g., "USDT-VES", "ZELLE-COP")
    pair_symbol = Column(String(20), unique=True, index=True, nullable=False)

    # Pair type (base, derived, cross)
    pair_type = Column(SQLEnum(PairType), nullable=False, default=PairType.BASE)

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
    base_pair = relationship("CurrencyPair", foreign_keys=[base_pair_id], remote_side="CurrencyPair.id", back_populates="derived_pairs")
    derived_pairs = relationship("CurrencyPair", foreign_keys=[base_pair_id], back_populates="base_pair")
    usdt_pair = relationship("CurrencyPair", foreign_keys=[usdt_pair_id], remote_side="CurrencyPair.id")
    
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
            "uuid": self.uuid,
            "pair_symbol": self.pair_symbol,
            "pair_type": self.pair_type.value if self.pair_type else None,
            "from_currency_uuid": self.from_currency.uuid if self.from_currency else None,
            "to_currency_uuid": self.to_currency.uuid if self.to_currency else None,
            "from_currency": self.from_currency.dict() if self.from_currency else None,
            "to_currency": self.to_currency.dict() if self.to_currency else None,
            "display_name": self.display_name,
            "is_active": self.is_active,
            "is_monitored": self.is_monitored,
            "binance_tracked": self.binance_tracked,
            "banks_to_track": self.banks_to_track,
            "amount_to_track": float(self.amount_to_track) if self.amount_to_track else None,
            "description": self.description,
            "base_pair_uuid": self.base_pair.uuid if self.base_pair else None,
            "base_pair": self.base_pair.dict() if self.base_pair else None,
            "derived_percentage": float(self.derived_percentage) if self.derived_percentage else None,
            "use_inverse_percentage": self.use_inverse_percentage,
            "rounding_mode": self.rounding_mode,
            "rounding_step": float(self.rounding_step) if self.rounding_step is not None else None,
            "rounding_direction": self.rounding_direction,
            "rounding_amount_side": self.rounding_amount_side,
            "usdt_reference_side": self.usdt_reference_side,
            "usdt_manual_rate": self.usdt_manual_rate,
            "usdt_pair_uuid": self.usdt_pair.uuid if self.usdt_pair else None,
            "usdt_pair_symbol": self.usdt_pair.pair_symbol if self.usdt_pair else None,
            "usdt_pair_inverse": self.usdt_pair_inverse,
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

    def validate_base_pair(self) -> tuple[bool, str]:
        """Validate that the base pair is valid for derived rates"""
        if not self.base_pair_id:
            # If no base pair, derived configuration should not be set
            if self.derived_percentage is not None:
                return False, "derived_percentage should be null when no base pair is set"
            return True, ""  # No base pair is valid (primary pair)
        
        if not self.base_pair:
            return False, "Base pair not found"
        
        # Base pair must be either binance tracked or have manual rates
        if not self.base_pair.binance_tracked:
            # Check if base pair has manual rates in exchange_rates table
            # This validation could be enhanced with a database query
            return True, "Base pair validation requires database check for manual rates"
        
        # Validate percentage range if provided
        if self.derived_percentage is not None:
            if self.derived_percentage < 0 or self.derived_percentage > 100:
                return False, "derived_percentage must be between 0 and 100"
        
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