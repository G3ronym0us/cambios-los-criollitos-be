from sqlalchemy import Column, Integer, Float, DateTime, String
from sqlalchemy.sql import func
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class BcvRate(UUIDMixin, Base):
    """
    Tasa oficial BCV (USD/VES) cacheada desde la fuente externa
    (https://ve.dolarapi.com/v1/dolares/oficial). Se refresca cada 5 min
    vía una Celery task. Se mantiene histórico delgado.

    Tabla dedicada para no contaminar exchange_rates con un valor que
    es referencia (no un par activo del negocio).
    """
    __tablename__ = "bcv_rates"

    id = Column(Integer, primary_key=True, index=True)
    rate = Column(Float, nullable=False)
    source = Column(String(60), nullable=False, default="ve.dolarapi.com")
    fetched_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)

    def __repr__(self):
        return f"<BcvRate(rate={self.rate}, fetched_at={self.fetched_at})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "rate": self.rate,
            "source": self.source,
            "fetched_at": self.fetched_at,
        }
