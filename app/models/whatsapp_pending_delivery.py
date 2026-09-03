"""
Lo que se salda desde el perfil del cliente, marcado a mano y deshacible.

Son DOS gestos opuestos y cada fila dice el suyo en `kind`:

- `DELIVERY` — **le entregamos** a él sin comprobante que lo respalde: efectivo en mano, un
  canal que el bot no lee. Vive en `uncovered_amount` de cada operación, igual que cuando se
  declara desde el panel de cobertura.
- `COLLECTION` — **nos pagó** él: el efectivo de un par que se cambia en efectivo
  (`settles_in_cash`), que hasta ahora no tenía dónde anotarse. Vive en `collected_amount`.

Mezclarlos en un solo gesto era el fondo del problema: en un par de efectivo marcar «pagado»
escribía `uncovered_amount` —o sea, declaraba cubierta NUESTRA pata— y la del cliente no
quedaba registrada en ninguna parte, así que la operación seguía en PENDING para siempre.

Estas dos tablas son el rastro. No son contables: el dinero vive en las columnas de la
operación. Aquí se guarda quién marcó qué, cuándo, y **qué había antes** — que es lo único
que permite deshacer de verdad en vez de poner ceros.
"""

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin

#: Le entregamos al cliente sin comprobante. Es el valor histórico: todo lote anterior a la
#: separación de los dos gestos es una entrega.
DELIVERY_KIND = "DELIVERY"
#: El cliente nos pagó su efectivo, en un par que se cambia en efectivo.
COLLECTION_KIND = "COLLECTION"


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

    Los `previous_*` no son un detalle de auditoría: son el mecanismo. Tanto
    `uncovered_amount` como `collected_amount` guardan el TOTAL de la operación, no un
    incremento, así que deshacer no puede ser «restar lo marcado» ni «poner a cero» — hay
    que reponer exactamente lo que había, que puede no ser nada o puede ser un lote anterior.
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
    #: `DELIVERY` (le entregamos) o `COLLECTION` (nos pagó). Va en la fila y no en el lote
    #: porque el gesto lo decide el PAR de cada operación: un cliente con una USD-VES en
    #: efectivo y un cambio normal a medio cubrir salda las dos de una vez, y son gestos
    #: opuestos. Ponerlo arriba obligaba a partir el lote o a mentir en una de las dos.
    kind = Column(String(12), nullable=False, server_default=DELIVERY_KIND)
    #: Lo entregado —o cobrado, según el `kind`— en esta operación.
    amount = Column(Float, nullable=False)
    previous_uncovered = Column(Float, nullable=True)
    previous_uncovered_reason = Column(String(24), nullable=True)
    #: Lo mismo para un lote de cobro: cuánto efectivo había recogido antes de éste.
    previous_collected = Column(Float, nullable=True)
    #: Un cobro que termina de recoger el efectivo CIERRA la operación, así que deshacerlo
    #: tiene que poder reabrirla. Se guardan los dos estados por nombre —no el enum— para
    #: que la fila siga siendo legible aunque el enum cambie.
    previous_status = Column(String(12), nullable=True)
    previous_delivery_status = Column(String(12), nullable=True)

    delivery = relationship("WhatsAppPendingDelivery", back_populates="items")
    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])

    def dict(self):
        op = self.operation
        cp = op.currency_pair if op else None
        return {
            "uuid": self.uuid,
            "operation_uuid": op.uuid if op else None,
            "pair_symbol": cp.pair_symbol if cp else None,
            "kind": self.kind,
            "amount": self.amount,
            "currency": (op.currency if op and op.currency else (
                cp.from_currency.symbol if cp and cp.from_currency else None
            )),
            "previous_uncovered": self.previous_uncovered,
            "previous_uncovered_reason": self.previous_uncovered_reason,
            "previous_collected": self.previous_collected,
        }
