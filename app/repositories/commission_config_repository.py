from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc
from typing import Optional, List, Tuple
from datetime import datetime
from uuid import UUID

from app.models.commission_config import CommissionConfiguration, CommissionConfigurationSplit
from app.models.user import User
from app.schemas.commission_config import CommissionConfigCreate, CommissionConfigUpdate


class CommissionConfigRepository:
    """Repository para gestionar configuraciones de comisiones por par"""

    def __init__(self, db: Session):
        self.db = db

    def create_configuration(
        self,
        config_data: CommissionConfigCreate,
        created_by_user_id: Optional[int] = None
    ) -> CommissionConfiguration:
        """
        Crear nueva configuración de comisiones

        Args:
            config_data: Datos de la configuración
            created_by_user_id: ID del usuario que crea la configuración

        Returns:
            Configuración creada con splits
        """
        # Obtener currency_pair_id del UUID
        from app.models.currency_pair import CurrencyPair
        currency_pair = self.db.query(CurrencyPair).filter(
            CurrencyPair.uuid == config_data.currency_pair_uuid
        ).first()

        if not currency_pair:
            raise ValueError(f"Currency pair with UUID {config_data.currency_pair_uuid} not found")

        # Resolver fund_group_id si se proporciona
        fund_group_id = None
        if config_data.fund_group_uuid:
            from app.models.fund import FundGroup
            fund_group = self.db.query(FundGroup).filter(
                FundGroup.uuid == str(config_data.fund_group_uuid)
            ).first()
            if not fund_group:
                raise ValueError(f"Fund group with UUID {config_data.fund_group_uuid} not found")
            fund_group_id = fund_group.id

        # Crear configuración
        db_config = CommissionConfiguration(
            currency_pair_id=currency_pair.id,
            fund_group_id=fund_group_id,
            name=config_data.name,
            description=config_data.description,
            total_percentage=config_data.total_percentage,
            is_active=True,
            created_by_user_id=created_by_user_id
        )

        self.db.add(db_config)
        self.db.flush()  # Para obtener el ID sin commit

        # Crear splits
        for split in config_data.splits:
            # Obtener user_id del UUID
            from app.models.user import User
            user = self.db.query(User).filter(User.uuid == split.user_uuid).first()

            if not user:
                raise ValueError(f"User with UUID {split.user_uuid} not found")

            db_split = CommissionConfigurationSplit(
                configuration_id=db_config.id,
                user_id=user.id,
                percentage=split.percentage
            )
            self.db.add(db_split)

        self.db.commit()
        self.db.refresh(db_config)
        return db_config

    def get_by_id(self, config_id: int) -> Optional[CommissionConfiguration]:
        """Obtener configuración por ID con splits y usuarios cargados"""
        return self.db.query(CommissionConfiguration)\
            .options(
                joinedload(CommissionConfiguration.splits).joinedload(CommissionConfigurationSplit.user),
                joinedload(CommissionConfiguration.currency_pair)
            )\
            .filter(CommissionConfiguration.id == config_id)\
            .first()

    def get_by_uuid(self, config_uuid: UUID) -> Optional[CommissionConfiguration]:
        """Obtener configuración por UUID con splits y usuarios cargados"""
        return self.db.query(CommissionConfiguration)\
            .options(
                joinedload(CommissionConfiguration.splits).joinedload(CommissionConfigurationSplit.user),
                joinedload(CommissionConfiguration.currency_pair)
            )\
            .filter(CommissionConfiguration.uuid == config_uuid)\
            .first()

    def get_by_pair(
        self,
        currency_pair_id: int,
        only_active: bool = True
    ) -> List[CommissionConfiguration]:
        """
        Obtener todas las configuraciones para un par de divisas

        Args:
            currency_pair_id: ID del par de divisas
            only_active: Solo configuraciones activas

        Returns:
            Lista de configuraciones con sus splits
        """
        query = self.db.query(CommissionConfiguration)\
            .options(
                joinedload(CommissionConfiguration.splits).joinedload(CommissionConfigurationSplit.user),
                joinedload(CommissionConfiguration.currency_pair)
            )\
            .filter(CommissionConfiguration.currency_pair_id == currency_pair_id)

        if only_active:
            query = query.filter(CommissionConfiguration.is_active == True)

        return query.order_by(CommissionConfiguration.created_at).all()

    def get_all_configurations(
        self,
        skip: int = 0,
        limit: int = 100,
        currency_pair_id: Optional[int] = None,
        only_active: bool = False
    ) -> Tuple[List[CommissionConfiguration], int]:
        """
        Obtener configuraciones con filtros y paginación

        Returns:
            Tuple de (configuraciones, total_count)
        """
        query = self.db.query(CommissionConfiguration)\
            .options(
                joinedload(CommissionConfiguration.splits).joinedload(CommissionConfigurationSplit.user),
                joinedload(CommissionConfiguration.currency_pair)
            )

        # Filtros
        if currency_pair_id:
            query = query.filter(CommissionConfiguration.currency_pair_id == currency_pair_id)

        if only_active:
            query = query.filter(CommissionConfiguration.is_active == True)

        # Contar total
        total = query.count()

        # Paginación y ordenar
        configurations = query.order_by(desc(CommissionConfiguration.created_at))\
            .offset(skip)\
            .limit(limit)\
            .all()

        return configurations, total

    def get_available_pairs(self) -> List[int]:
        """
        Obtener lista de IDs de pares de divisas con configuraciones

        Returns:
            Lista de IDs de pares únicos
        """
        pairs = self.db.query(CommissionConfiguration.currency_pair_id.distinct())\
            .filter(CommissionConfiguration.is_active == True)\
            .order_by(CommissionConfiguration.currency_pair_id)\
            .all()

        return [pair[0] for pair in pairs]

    def update_configuration(
        self,
        config_id: int,
        config_data: CommissionConfigUpdate
    ) -> Optional[CommissionConfiguration]:
        """
        Actualizar configuración

        Args:
            config_id: ID de la configuración
            config_data: Datos a actualizar

        Returns:
            Configuración actualizada o None si no existe
        """
        config = self.get_by_id(config_id)
        if not config:
            return None

        update_data = config_data.dict(exclude_unset=True, exclude={'splits', 'fund_group_uuid'})

        # Resolver fund_group_uuid → fund_group_id si se proporciona
        if 'fund_group_uuid' in config_data.dict(exclude_unset=True):
            if config_data.fund_group_uuid is None:
                update_data['fund_group_id'] = None
            else:
                from app.models.fund import FundGroup
                fund_group = self.db.query(FundGroup).filter(
                    FundGroup.uuid == str(config_data.fund_group_uuid)
                ).first()
                if not fund_group:
                    raise ValueError(f"Fund group with UUID {config_data.fund_group_uuid} not found")
                update_data['fund_group_id'] = fund_group.id

        # Actualizar campos básicos
        for field, value in update_data.items():
            setattr(config, field, value)

        # Si se actualizan splits, eliminar los antiguos y crear nuevos
        if config_data.splits is not None:
            # Eliminar splits existentes
            self.db.query(CommissionConfigurationSplit)\
                .filter(CommissionConfigurationSplit.configuration_id == config_id)\
                .delete()

            # Crear nuevos splits
            for split in config_data.splits:
                # Obtener user_id del UUID
                from app.models.user import User
                user = self.db.query(User).filter(User.uuid == split.user_uuid).first()

                if not user:
                    raise ValueError(f"User with UUID {split.user_uuid} not found")

                db_split = CommissionConfigurationSplit(
                    configuration_id=config_id,
                    user_id=user.id,
                    percentage=split.percentage
                )
                self.db.add(db_split)

        config.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(config)
        return config

    def delete_configuration(self, config_id: int) -> bool:
        """
        Eliminar configuración (también elimina splits por CASCADE)

        Args:
            config_id: ID de la configuración

        Returns:
            True si se eliminó, False si no existe
        """
        config = self.get_by_id(config_id)
        if not config:
            return False

        self.db.delete(config)
        self.db.commit()
        return True

    def deactivate_configuration(self, config_id: int) -> bool:
        """
        Desactivar configuración (soft delete)

        Args:
            config_id: ID de la configuración

        Returns:
            True si se desactivó, False si no existe
        """
        config = self.get_by_id(config_id)
        if not config:
            return False

        config.is_active = False
        config.updated_at = datetime.utcnow()
        self.db.commit()
        return True

    def get_config_stats(self) -> dict:
        """Obtener estadísticas generales de configuraciones"""
        total = self.db.query(func.count(CommissionConfiguration.id)).scalar()
        active = self.db.query(func.count(CommissionConfiguration.id))\
            .filter(CommissionConfiguration.is_active == True).scalar()

        pairs_count = self.db.query(func.count(CommissionConfiguration.currency_pair_id.distinct()))\
            .filter(CommissionConfiguration.is_active == True).scalar()

        return {
            "total_configurations": total,
            "active_configurations": active,
            "unique_pairs": pairs_count
        }
