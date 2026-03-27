from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, desc
from typing import List, Optional
from datetime import datetime, timedelta
from uuid import UUID
from app.models.exchange_rate import ExchangeRate
from app.models.currency_pair import CurrencyPair
from app.schemas.exchange_rate import ExchangeRateCreate, ExchangeRateUpdate
from app.database.connection import get_db

class ExchangeRateRepository:
    def __init__(self, db: Session):
        self.db = db

    def save_rates(self, rates: List[ExchangeRate]) -> bool:
        """Guardar múltiples tasas de cambio"""
        try:
            # Desactivar tasas anteriores del mismo currency_pair que no tengan precio manual
            currency_pair_ids = set(rate.currency_pair_id for rate in rates if rate.currency_pair_id)
            for pair_id in currency_pair_ids:
                existing_rates = self.db.query(ExchangeRate).filter(
                    and_(
                        ExchangeRate.currency_pair_id == pair_id,
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

    def get_by_id(self, rate_id: int) -> Optional[ExchangeRate]:
        """Get exchange rate by ID"""
        return self.db.query(ExchangeRate).filter(ExchangeRate.id == rate_id).first()

    def get_by_uuid(self, rate_uuid: UUID) -> Optional[ExchangeRate]:
        """Get exchange rate by UUID"""
        return self.db.query(ExchangeRate).filter(ExchangeRate.uuid == rate_uuid).first()

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

    def get_rate_at_datetime(self, currency_pair_id: int, at: datetime) -> Optional[ExchangeRate]:
        """Obtener la tasa que estaba activa en un momento específico.

        Es el registro con el created_at más reciente que sea <= at.
        """
        return self.db.query(ExchangeRate)\
            .filter(
                ExchangeRate.currency_pair_id == currency_pair_id,
                ExchangeRate.created_at <= at
            )\
            .order_by(desc(ExchangeRate.created_at))\
            .first()

    def set_manual_rate(self, from_currency: str, to_currency: str, manual_rate: float) -> Optional[ExchangeRate]:
        """Establecer una tasa manual para un par de monedas"""
        try:
            rate = self.get_latest_rate(from_currency, to_currency)
            
            if rate:
                # Si existe un registro, actualizarlo
                rate.set_manual_rate(manual_rate)
            else:
                # Si no existe, crear un nuevo registro manual
                rate = ExchangeRate(
                    from_currency=from_currency,
                    to_currency=to_currency,
                    rate=manual_rate,
                    is_active=True,
                    is_manual=True,
                    manual_rate=manual_rate,
                    automatic_rate=None
                )
                self.db.add(rate)
            
            self.db.commit()
            return rate
            
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

    # ===== NUEVOS MÉTODOS CON currency_pair_id =====

    def create_or_update_rate(self, rate_data: ExchangeRateCreate) -> ExchangeRate:
        """
        Crear o actualizar tasa de cambio para un par de divisas.
        Garantiza que solo haya un registro activo por par.

        Args:
            rate_data: Datos de la nueva tasa

        Returns:
            ExchangeRate creado o actualizado
        """
        # Obtener el currency_pair para extraer from_currency y to_currency
        currency_pair = self.db.query(CurrencyPair).filter(
            CurrencyPair.uuid == rate_data.currency_pair_uuid
        ).options(
            joinedload(CurrencyPair.from_currency),
            joinedload(CurrencyPair.to_currency)
        ).first()

        if not currency_pair:
            raise ValueError(f"Currency pair with UUID {rate_data.currency_pair_uuid} not found")

        # Desactivar cualquier rate activo anterior para este par
        self.db.query(ExchangeRate).filter(
            and_(
                ExchangeRate.currency_pair_id == currency_pair.id,
                ExchangeRate.is_active == True
            )
        ).update({"is_active": False})

        # Crear nuevo rate activo
        new_rate = ExchangeRate(
            currency_pair_id=currency_pair.id,
            from_currency=currency_pair.from_currency.symbol,
            to_currency=currency_pair.to_currency.symbol,
            rate=rate_data.rate,
            percentage=rate_data.percentage,
            inverse_percentage=rate_data.inverse_percentage,
            is_active=True
        )

        self.db.add(new_rate)
        self.db.commit()
        self.db.refresh(new_rate)

        return new_rate

    def get_active_rate_by_pair(self, currency_pair_uuid: UUID) -> Optional[ExchangeRate]:
        """
        Obtener la tasa activa para un par de divisas específico.
        Solo debe haber una tasa activa por par.

        Args:
            currency_pair_uuid: UUID del par de divisas

        Returns:
            ExchangeRate activo o None
        """
        currency_pair = self.db.query(CurrencyPair).filter(
            CurrencyPair.uuid == currency_pair_uuid
        ).first()

        if not currency_pair:
            return None

        return self.db.query(ExchangeRate).filter(
            and_(
                ExchangeRate.currency_pair_id == currency_pair.id,
                ExchangeRate.is_active == True
            )
        ).options(
            joinedload(ExchangeRate.currency_pair).joinedload(CurrencyPair.from_currency),
            joinedload(ExchangeRate.currency_pair).joinedload(CurrencyPair.to_currency)
        ).first()

    def update_rate(self, rate_uuid: UUID, update_data: ExchangeRateUpdate) -> Optional[ExchangeRate]:
        """
        Actualizar una tasa de cambio existente.

        Args:
            rate_uuid: UUID de la tasa a actualizar
            update_data: Datos de actualización

        Returns:
            ExchangeRate actualizado o None
        """
        rate = self.get_by_uuid(rate_uuid)
        if not rate:
            return None

        update_dict = update_data.dict(exclude_unset=True)

        # Si se actualiza el currency_pair_uuid, resolver las monedas
        if 'currency_pair_uuid' in update_dict:
            currency_pair = self.db.query(CurrencyPair).filter(
                CurrencyPair.uuid == update_dict['currency_pair_uuid']
            ).options(
                joinedload(CurrencyPair.from_currency),
                joinedload(CurrencyPair.to_currency)
            ).first()

            if not currency_pair:
                raise ValueError(f"Currency pair with UUID {update_dict['currency_pair_uuid']} not found")

            update_dict['currency_pair_id'] = currency_pair.id
            update_dict['from_currency'] = currency_pair.from_currency.symbol
            update_dict['to_currency'] = currency_pair.to_currency.symbol
            del update_dict['currency_pair_uuid']

        # Aplicar actualizaciones
        for field, value in update_dict.items():
            setattr(rate, field, value)

        rate.updated_at = datetime.utcnow()

        self.db.commit()
        self.db.refresh(rate)

        return rate

    def get_all_active_rates(self) -> List[ExchangeRate]:
        """Obtener todas las tasas activas con información de currency_pair"""
        return self.db.query(ExchangeRate).filter(
            ExchangeRate.is_active == True
        ).options(
            joinedload(ExchangeRate.currency_pair).joinedload(CurrencyPair.from_currency),
            joinedload(ExchangeRate.currency_pair).joinedload(CurrencyPair.to_currency)
        ).order_by(desc(ExchangeRate.created_at)).all()

    def delete_rate(self, rate_uuid: UUID) -> bool:
        """Eliminar (desactivar) una tasa de cambio"""
        rate = self.get_by_uuid(rate_uuid)
        if not rate:
            return False

        rate.is_active = False
        self.db.commit()
        return True
