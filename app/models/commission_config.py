from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class CommissionConfiguration(UUIDMixin, Base):
    """
    Configuración predefinida de distribución de comisiones por par de divisas

    Ejemplo: Para Zelle/VES podría haber:
    - Config "Estándar": Usuario1 (5%) + Usuario2 (5%) = 10%
    - Config "Triple": Usuario1 (3.5%) + Usuario2 (3.5%) + Usuario3 (3%) = 10%
    """
    __tablename__ = "commission_configurations"

    id = Column(Integer, primary_key=True, index=True)

    # Par de divisas (foreign key a currency_pairs)
    currency_pair_id = Column(Integer, ForeignKey("currency_pairs.id"), nullable=False, index=True)

    # Nombre descriptivo de la configuración
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)

    # Porcentaje total de ganancia para esta configuración
    total_percentage = Column(Float, nullable=False)

    # Estado
    is_active = Column(Boolean, default=True, nullable=False)

    # Auditoría
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Relaciones
    currency_pair = relationship("CurrencyPair", foreign_keys=[currency_pair_id])
    splits = relationship("CommissionConfigurationSplit", back_populates="configuration", cascade="all, delete-orphan")
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    def __repr__(self):
        return f"<CommissionConfiguration(id={self.id}, pair_id={self.currency_pair_id}, name={self.name})>"

    def dict(self):
        """Convertir a diccionario para respuestas JSON"""
        return {
            "uuid": self.uuid,
            "currency_pair_uuid": self.currency_pair.uuid if self.currency_pair else None,
            "pair_symbol": self.currency_pair.pair_symbol if self.currency_pair else None,
            "name": self.name,
            "description": self.description,
            "total_percentage": self.total_percentage,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "created_by_user_uuid": self.created_by.uuid if self.created_by else None
        }


class CommissionConfigurationSplit(UUIDMixin, Base):
    """División de comisión para una configuración específica"""
    __tablename__ = "commission_configuration_splits"

    id = Column(Integer, primary_key=True, index=True)
    configuration_id = Column(Integer, ForeignKey("commission_configurations.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Porcentaje asignado a este usuario en esta configuración
    percentage = Column(Float, nullable=False)

    # Auditoría
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    configuration = relationship("CommissionConfiguration", back_populates="splits")
    user = relationship("User")

    def __repr__(self):
        return f"<CommissionConfigurationSplit(config_id={self.configuration_id}, user_id={self.user_id}, percentage={self.percentage})>"

    def dict(self):
        """Convertir a diccionario para respuestas JSON"""
        return {
            "uuid": self.uuid,
            "configuration_uuid": self.configuration.uuid if self.configuration else None,
            "user_uuid": self.user.uuid if self.user else None,
            "percentage": self.percentage,
            "created_at": self.created_at
        }
