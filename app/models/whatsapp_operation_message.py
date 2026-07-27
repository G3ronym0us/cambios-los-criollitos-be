"""
De qué mensaje de WhatsApp nació una operación.

No guarda nada del trato: es un índice del id de mensaje —el del CLIENTE, el que pidió la
cotización— hacia la operación que ese mensaje creó. Vivía en el SQLite del bot
(`quote_message_map`) y de ahí colgaban tres cosas que no son informativas:

- **Revoke**: el cliente borra su mensaje y hay que decirle al operador qué operación había
  originado. Una revocación nunca cambia el estado de la operación.
- **Corrección**: antes de cancelar la cotización reemplazada hay que confirmar que ESTE
  mensaje sí creó una nueva y distinta, en vez de cancelar a ciegas.
- **VIA_PARTNER**: etiquetar la operación que creó ese mensaje y no «la op activa del
  teléfono»; cuando un socio dispara varias en el mismo segundo, resolver por teléfono
  devuelve la más reciente y deja una sin marcar.

Es tabla y no columna en `whatsapp_operations` porque el dato no describe el trato sino de
dónde vino, y porque una operación puede acabar teniendo más de un mensaje detrás.
"""

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppOperationMessage(UUIDMixin, Base):
    __tablename__ = "whatsapp_operation_messages"

    id = Column(Integer, primary_key=True, index=True)
    # Id serializado de whatsapp-web.js (`false_5215...@c.us_3EB0...`). Único: un mensaje
    # origina una sola operación, y reenganchar el mismo id reapunta la fila.
    wa_message_id = Column(String(255), nullable=False, unique=True, index=True)
    whatsapp_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # El teléfono que mandó el mensaje: el bot lo necesita para avisar al operador sin
    # tener que resolver la operación entera.
    client_phone = Column(String(64), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    operation = relationship("WhatsAppOperation", foreign_keys=[whatsapp_operation_id])

    def __repr__(self):
        return (
            f"<WhatsAppOperationMessage({self.wa_message_id} -> op {self.whatsapp_operation_id})>"
        )
