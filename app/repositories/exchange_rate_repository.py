from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from typing import List, Optional
from datetime import datetime, timedelta
from app.models.exchange_rate import ExchangeRate
from app.database.connection import get_db

class ExchangeRateRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_rates(self, rates: List[ExchangeRate]) -> bool:
        """Guardar múltiples tasas de cambio"""
        try:
            # Desactivar tasas anteriores del mismo source
            sources = set(rate.source for rate in rates)
            for source in sources:
                self.db.query(ExchangeRate).filter(
                    and_(
                        ExchangeRate.source == source,
                        ExchangeRate.is_active == True
                    )
                ).update({"is_active": False})

            # Agregar nuevas tasas
            for rate in rates:
                rate.is_active = True
                rate.created_at = datetime.utcnow()
                self.db.add(rate)

            self.db.commit()
            print(f"✅ {len(rates)} tasas guardadas en base de datos")
            return True

        except Exception as e:
            print(f"❌ Error guardando tasas: {e}")
            self.db.rollback()
            return False

    def get_active_rates(self, from_currency: Optional[str] = None, to_currency: Optional[str] = None) -> List[ExchangeRate]:
        """Obtener tasas activas"""
        query = self.db.query(ExchangeRate).filter(ExchangeRate.is_active == True)
        
        if from_currency:
            query = query.filter(ExchangeRate.from_currency == from_currency)
        if to_currency:
            query = query.filter(ExchangeRate.to_currency == to_currency)
            
        return query.order_by(desc(ExchangeRate.created_at)).all()

    def get_latest_rate(self, from_currency: str, to_currency: str) -> Optional[ExchangeRate]:
        """Obtener la tasa más reciente entre dos monedas"""
        return self.db.query(ExchangeRate).filter(
            and_(
                ExchangeRate.from_currency == from_currency,
                ExchangeRate.to_currency == to_currency,
                ExchangeRate.is_active == True
            )
        ).order_by(desc(ExchangeRate.created_at)).first()

    def cleanup_old_rates(self, days: int = 7):
        """Limpiar tasas antiguas"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = self.db.query(ExchangeRate).filter(
            ExchangeRate.created_at < cutoff_date
        ).delete()
        self.db.commit()
        print(f"🗑️ {deleted} tasas antiguas eliminadas")
