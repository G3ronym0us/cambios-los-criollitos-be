from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
from sqlalchemy.sql import func
from app.database.connection import Base

class ExchangeRate(Base):
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    from_currency = Column(String(10), nullable=False, index=True)
    to_currency = Column(String(10), nullable=False, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(50), nullable=False)  # 'binance', 'manual', etc.
    is_active = Column(Boolean, default=True)
    inverse_percentage = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    percentage = Column(Float, nullable=True)
    manual_rate = Column(Float, nullable=True)
    is_manual = Column(Boolean, default=False)
    automatic_rate = Column(Float, nullable=True)

    def __repr__(self):
        return f"<ExchangeRate({self.from_currency}->{self.to_currency}: {self.rate})>"

    def set_manual_rate(self, manual_rate):
        """Establece una tasa manual y guarda la automática"""
        if not self.is_manual:
            self.automatic_rate = self.rate
        self.manual_rate = manual_rate
        self.is_manual = True
        self.rate = manual_rate

    def remove_manual_rate(self):
        """Remueve la tasa manual y vuelve a la automática"""
        if self.automatic_rate:
            self.rate = self.automatic_rate
        self.is_manual = False
        self.manual_rate = None

    def update_automatic_rate(self, new_rate):
        """Actualiza la tasa automática manteniendo la manual si está activa"""
        if self.is_manual:
            self.automatic_rate = new_rate
        else:
            self.rate = new_rate

    @classmethod
    def create_safe(cls, from_currency, to_currency, rate, source="binance", percentage=None, inverse_percentage=False):
        """Método factory para crear tasas de cambio de forma segura"""
        if rate is None or rate <= 0:
            return None
        
        if percentage is not None:
            # Convert percentage to float to ensure compatibility with rate operations
            percentage_float = float(percentage)
            if inverse_percentage:
                rate = rate * (1 + (percentage_float / 100))
            else:
                rate = rate * (1 - (percentage_float / 100))
                
        return cls(
            from_currency=from_currency.value if hasattr(from_currency, 'value') else from_currency,
            to_currency=to_currency.value if hasattr(to_currency, 'value') else to_currency,
            rate=rate,
            source=source,
            percentage=float(percentage) if percentage is not None else None,
            inverse_percentage=inverse_percentage
        )
