"""
Entregas de lo que le debemos al cliente, marcadas desde su perfil.

El caso: una operación se cubre normalmente con un comprobante de salida, pero a veces se
le entrega al cliente en efectivo o por un canal que el bot no lee. Eso ya se podía
declarar operación por operación (`uncovered_amount`), y lo que faltaba era hacerlo de
varias a la vez —o repartiendo un monto entre ellas— dejando rastro y pudiendo deshacerlo.

Estas dos tablas son ese rastro. No son contables: el dinero sigue viviendo en
`uncovered_amount` de cada operación, como cuando se declara desde el panel de cobertura.
Aquí sólo se guarda quién marcó qué, cuándo, y **qué había antes** — que es lo único que
permite deshacer de verdad en vez de poner ceros.
"""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppPendingDelivery(UUIDMixin, Base):
    """Un lote: lo que se marcó como entregado de una vez, y si se deshizo."""

    __tablename__ = "whatsapp_pending_deliveries"

    id = Column(Integer, primary_key=True, index=True)
    client_id = Column(
        Integer, ForeignKey("whatsapp_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    note = Column(Text, nullable=True)

    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    # Deshacer NO borra: marca. Un lote deshecho sigue contando lo que pasó y quién lo
    # revirtió — que es justo lo que hace falta para auditar un error.
    undone_at = Column(DateTime(timezone=True), nullable=True)
    undone_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    client = relationship("WhatsAppClient", foreign_keys=[client_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    undone_by = relationship("User", foreign_keys=[undone_by_user_id])
    items = relationship(
        "WhatsAppPendingDeliveryItem",
        back_populates="delivery",
        cascade="all, delete-orphan",
        order_by="WhatsAppPendingDeliveryItem.id",
    )

    @property
    def is_undone(self) -> bool:
        return self.undone_at is not None

    @property
    def total_amount(self) -> float:
        return round(sum(item.amount or 0 for item in (self.items or [])), 2)

    def dict(self):
        return {
            "uuid": self.uuid,
            "client_uuid": self.client.uuid if self.client else None,
            "note": self.note,
            "amount": self.total_amount,
            "operations": len(self.items or []),
            "created_by_username": self.created_by.username if self.created_by else None,
            "created_at": self.created_at,
            "undone_at": self.undone_at,
            "undone_by_username": self.undone_by.username if self.undone_by else None,
            "items": [item.dict() for item in (self.items or [])],
        }


class WhatsAppPendingDeliveryItem(UUIDMixin, Base):
    """
    Una operación dentro del lote, con el estado al que hay que volver si se deshace.

    `previous_uncovered` / `previous_uncovered_reason` no son un detalle de auditoría: son
    el mecanismo. `uncovered_amount` es el hueco ENTERO de la operación, no un incremento,
    así que deshacer no puede ser «restar lo entregado» ni «poner a cero» — hay que reponer
    exactamente lo que había, que puede no ser nada o puede ser una entrega anterior.
    """

    __tablename__ = "whatsapp_pending_delivery_items"

    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(
        Integer,
        ForeignKey("whatsapp_pending_deliveries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    #: Lo entregado en esta operación dentro de este lote.
    amount = Column(Float, nullable=False)
    previous_uncovered = Column(Float, nullable=True)
    previous_uncovered_reason = Column(String(24), nullable=True)

    delivery = relationship("WhatsAppPendingDelivery", back_populates="items")
    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])

    def dict(self):
        op = self.operation
        cp = op.currency_pair if op else None
        return {
            "uuid": self.uuid,
            "operation_uuid": op.uuid if op else None,
            "pair_symbol": cp.pair_symbol if cp else None,
            "amount": self.amount,
            "currency": (op.currency if op and op.currency else (
                cp.from_currency.symbol if cp and cp.from_currency else None
            )),
            "previous_uncovered": self.previous_uncovered,
            "previous_uncovered_reason": self.previous_uncovered_reason,
        }
