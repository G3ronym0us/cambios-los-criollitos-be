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
            # Desactivar tasas anteriores del mismo source que no tengan precio manual
            sources = set(rate.source for rate in rates)
            for source in sources:
                existing_rates = self.db.query(ExchangeRate).filter(
                    and_(
                        ExchangeRate.source == source,
                        ExchangeRate.is_active == True
                    )
                ).all()
                
                # Desactivar todas las tasas existentes para el historial
                for existing_rate in existing_rates:
                    existing_rate.is_active = False

            # Siempre crear nuevos registros para mantener historial
            for rate in rates:
                # Verificar si existe una tasa manual activa para este par
                existing_manual = self.db.query(ExchangeRate).filter(
                    and_(
                        ExchangeRate.from_currency == rate.from_currency,
                        ExchangeRate.to_currency == rate.to_currency,
                        ExchangeRate.is_manual == True,
                        ExchangeRate.is_active == True
                    )
                ).first()
                
                if existing_manual:
                    # Crear nuevo registro manteniendo la tasa manual
                    rate.manual_rate = existing_manual.manual_rate
                    rate.is_manual = True
                    rate.automatic_rate = rate.rate  # Guardar la tasa automática
                    rate.rate = existing_manual.manual_rate  # Usar la tasa manual como activa
                
                rate.is_active = True
                rate.created_at = datetime.utcnow()
                self.db.add(rate)

            self.db.commit()
            print(f"✅ {len(rates)} tasas procesadas en base de datos")
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

    def get_latest_rates_for_pair(self, from_currency: str, to_currency: str, limit: int = 10) -> List[ExchangeRate]:
        """Obtener las últimas tasas para un par de monedas específico"""
        return self.db.query(ExchangeRate).filter(
            and_(
                ExchangeRate.from_currency == from_currency,
                ExchangeRate.to_currency == to_currency
            )
        ).order_by(desc(ExchangeRate.created_at)).limit(limit).all()

    def set_manual_rate(self, from_currency: str, to_currency: str, manual_rate: float) -> Optional[ExchangeRate]:
        """Establecer una tasa manual para un par de monedas"""
        try:
            rate = self.get_latest_rate(from_currency, to_currency)
            if rate:
                rate.set_manual_rate(manual_rate)
                self.db.commit()
                return rate
            return None
        except Exception as e:
            print(f"❌ Error estableciendo tasa manual: {e}")
            self.db.rollback()
            return None

    def remove_manual_rate(self, from_currency: str, to_currency: str) -> Optional[ExchangeRate]:
        """Remover la tasa manual de un par de monedas"""
        try:
            rate = self.get_latest_rate(from_currency, to_currency)
            if rate and rate.is_manual:
                rate.remove_manual_rate()
                self.db.commit()
                return rate
            return None
        except Exception as e:
            print(f"❌ Error removiendo tasa manual: {e}")
            self.db.rollback()
            return None

    def cleanup_old_rates(self, days: int = 7):
        """Limpiar tasas antiguas"""
        cutoff_date = datetime.utcnow() - timedelta(days=days)
        deleted = self.db.query(ExchangeRate).filter(
            ExchangeRate.created_at < cutoff_date
        ).delete()
        self.db.commit()
        print(f"🗑️ {deleted} tasas antiguas eliminadas")
