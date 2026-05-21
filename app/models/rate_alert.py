from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class RateAlert(UUIDMixin, Base):
    __tablename__ = "rate_alerts"

    id = Column(Integer, primary_key=True, index=True)
    currency_pair_id = Column(Integer, ForeignKey("currency_pairs.id", ondelete="CASCADE"), nullable=False, index=True)
    from_currency = Column(String(10), nullable=False)
    to_currency = Column(String(10), nullable=False)
    manual_rate = Column(Float, nullable=False)
    automatic_rate = Column(Float, nullable=False)
    diff_percentage = Column(Float, nullable=False)
    is_acknowledged = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<RateAlert({self.from_currency}->{self.to_currency}: {self.diff_percentage:.2f}%)>"
