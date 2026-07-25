import enum

from sqlalchemy import CheckConstraint, Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.fund import CaseInsensitiveEnum
from app.models.mixins import UUIDMixin


class ProfitAllocationDestination(enum.Enum):
    """A dónde va cada pedazo del margen que se le cobró al cliente."""
    FUND   = "FUND"    # se lo queda un fondo y se reparte entre sus miembros
    CLIENT = "CLIENT"  # se le devuelve al cliente (intermediario de un tercero)


class OperationProfitAllocation(UUIDMixin, Base):
    """
    Reparto del margen de una operación entre sus destinos.

    La operación dice cuánto se le cobró al cliente (`applied_percentage`); estas filas dicen
    quién se queda con qué parte. El caso corriente es una sola fila —el fondo que atendió la
    operación, por su porcentaje por defecto— pero el modelo admite varias: una operación
    ZELLE→BRL que cobra 10% puede dejar 8% en el fondo Zelle y 2% en el de Brasil, o dejar 7%
    en el fondo y devolverle 2% al cliente.

    La suma NO tiene por qué dar el margen cobrado: lo que sobra es margen sin asignar, y lo
    que falta (repartir más de lo cobrado) es una diferencia negativa que el operador acepta.
    """
    __tablename__ = "operation_profit_allocations"

    id = Column(Integer, primary_key=True, index=True)
    whatsapp_operation_id = Column(
        Integer,
        ForeignKey("whatsapp_operations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    destination_type = Column(
        CaseInsensitiveEnum(ProfitAllocationDestination), nullable=False, index=True
    )
    fund_group_id = Column(Integer, ForeignKey("fund_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    whatsapp_client_id = Column(Integer, ForeignKey("whatsapp_clients.id", ondelete="CASCADE"), nullable=True, index=True)

    percentage = Column(Float, nullable=False)          # sobre el valor de la operación
    amount_usdt = Column(Float, nullable=True)          # el mismo pedazo en USDT, al valor de la op

    # Marca de que el operador aceptó un reparto que no cuadra con lo cobrado.
    approved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at = Column(DateTime(timezone=True), nullable=True)
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    operation = relationship("WhatsAppOperation", back_populates="profit_allocations")
    fund_group = relationship("FundGroup")
    client = relationship("WhatsAppClient")
    approved_by = relationship("User")

    __table_args__ = (
        # El destino y su referencia van juntos: un FUND apunta a un fondo, un CLIENT a un cliente.
        CheckConstraint(
            "(destination_type = 'FUND' AND fund_group_id IS NOT NULL AND whatsapp_client_id IS NULL)"
            " OR (destination_type = 'CLIENT' AND whatsapp_client_id IS NOT NULL AND fund_group_id IS NULL)",
            name="ck_profit_allocation_destination",
        ),
    )

    @property
    def destination_name(self):
        if self.destination_type == ProfitAllocationDestination.FUND:
            return self.fund_group.name if self.fund_group else None
        return self.client.display_name or self.client.phone if self.client else None

    def __repr__(self):
        return (
            f"<OperationProfitAllocation(op={self.whatsapp_operation_id}, "
            f"{self.destination_type.value}={self.destination_name}, {self.percentage}%)>"
        )

    def dict(self):
        return {
            "uuid": self.uuid,
            "destination_type": self.destination_type.value if self.destination_type else None,
            "fund_group_uuid": self.fund_group.uuid if self.fund_group else None,
            "client_uuid": self.client.uuid if self.client else None,
            "destination_name": self.destination_name,
            "percentage": self.percentage,
            "amount_usdt": self.amount_usdt,
            "approved_by_uuid": self.approved_by.uuid if self.approved_by else None,
            "approved_at": self.approved_at,
            "notes": self.notes,
            "created_at": self.created_at,
        }
