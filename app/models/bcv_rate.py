from sqlalchemy import Column, Integer, Float, DateTime, String, Index
from sqlalchemy.sql import func
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class BcvRate(UUIDMixin, Base):
    """
    Tasa oficial BCV cacheada desde la fuente externa
    (ve.dolarapi.com). Se refresca cada 5 min vía una Celery task. Se
    mantiene histórico delgado.

    `currency` distingue las tasas del BCV (USD y EUR). Cada moneda es su
    propia serie: la tasa en euros no se deriva de la del dólar, así que
    consultar el histórico exige filtrar por moneda o se mezclarían.

    Tabla dedicada para no contaminar exchange_rates con un valor que
    es referencia (no un par activo del negocio).
    """
    __tablename__ = "bcv_rates"
    __table_args__ = (
        Index("ix_bcv_rates_currency_fetched_at", "currency", "fetched_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String(8), nullable=False, default="USD", server_default="USD")
    rate = Column(Float, nullable=False)
    source = Column(String(60), nullable=False, default="ve.dolarapi.com")
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    def __repr__(self):
        return f"<BcvRate({self.currency} rate={self.rate}, fetched_at={self.fetched_at})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "currency": self.currency,
            "rate": self.rate,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }
