"""
Mudanzas de un comprobante de un cliente a otro.

El comprobante entra a nombre de quien lo mandó, pero el dinero puede ser de otro: el esposo
pagó por la esposa, la empresa por el empleado, el bot lo pegó al cliente equivocado. Hasta
ahora la salida era anular y volver a cargar, y se perdía el hilo.

La pieza clave está en el pago, no aquí: `owner_client_id` es un OVERRIDE del dueño, y
`client_phone` —de dónde salió el dinero de verdad, lo que leyó el OCR— **no se toca nunca**.
Por eso el pago nunca se duplica ni se anula, y por eso buscar por el nombre del origen lo
sigue encontrando. Esta tabla es el rastro: quién lo movió, cuándo, por qué, y de qué
operación hubo que desengancharlo.
"""

import enum

from sqlalchemy import Column, DateTime, Enum as SQLEnum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.connection import Base
from app.models.mixins import UUIDMixin


class PaymentTransferReason(enum.Enum):
    #: El dinero lo mandó un tercero (familiar, empresa) en nombre del cliente real.
    THIRD_PARTY = "THIRD_PARTY"
    #: El matcher del bot lo asignó al cliente equivocado.
    BOT_MISMATCH = "BOT_MISMATCH"
    #: El mismo cliente existe dos veces y el pago cayó en la ficha que no se usa.
    DUPLICATE_CLIENT = "DUPLICATE_CLIENT"


class WhatsAppPaymentTransfer(UUIDMixin, Base):
    """
    Una mudanza. Se apilan: un pago se puede transferir varias veces y cada salto deja su
    fila, así que la cadena completa se lee en orden. La cabecera del pago muestra el PRIMER
    origen (la fila más vieja), que es de dónde salió el dinero realmente.

    Como en `FundPendingDeposit`, el lado del pago va en dos FK nullable en vez de un par
    (tabla, id): así la integridad referencial la lleva la base y no el código.
    """

    __tablename__ = "whatsapp_payment_transfers"

    id = Column(Integer, primary_key=True, index=True)

    incoming_payment_id = Column(
        Integer,
        ForeignKey("whatsapp_incoming_payments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    outgoing_payment_id = Column(
        Integer,
        ForeignKey("whatsapp_outgoing_payments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    # Dueño del que salió. NULL cuando el pago no tenía cliente conocido (teléfono sin ficha).
    from_client_id = Column(
        Integer, ForeignKey("whatsapp_clients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Copia del teléfono y del nombre en el momento de la mudanza: si mañana la ficha del
    # origen se borra o se renombra, el rastro tiene que seguir diciendo de dónde vino.
    from_client_phone = Column(String(64), nullable=True)
    from_client_name = Column(String(120), nullable=True)

    to_client_id = Column(
        Integer, ForeignKey("whatsapp_clients.id", ondelete="CASCADE"), nullable=False, index=True
    )

    reason = Column(SQLEnum(PaymentTransferReason), nullable=False)
    note = Column(Text, nullable=True)

    # De qué operación hubo que desengancharlo. Se guarda para poder contar en la bitácora
    # por qué esa operación se quedó esperando fondos.
    unlinked_operation_id = Column(
        Integer, ForeignKey("whatsapp_operations.id", ondelete="SET NULL"), nullable=True
    )

    created_by_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    from_client = relationship("WhatsAppClient", foreign_keys=[from_client_id])
    to_client = relationship("WhatsAppClient", foreign_keys=[to_client_id])
    unlinked_operation = relationship("WhatsAppOperation", foreign_keys=[unlinked_operation_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])

    @property
    def payment_id(self) -> int:
        return self.incoming_payment_id or self.outgoing_payment_id

    def dict(self) -> dict:
        return {
            "uuid": str(self.uuid),
            "from_client_uuid": str(self.from_client.uuid) if self.from_client else None,
            # El nombre sale de la ficha viva si sigue existiendo, y si no del que se copió.
            "from_client_name": (
                self.from_client.display_name if self.from_client else None
            ) or self.from_client_name,
            "from_client_phone": (
                self.from_client.phone if self.from_client else None
            ) or self.from_client_phone,
            "to_client_uuid": str(self.to_client.uuid) if self.to_client else None,
            "to_client_name": self.to_client.display_name if self.to_client else None,
            "reason": self.reason.value if self.reason else None,
            "note": self.note,
            "unlinked_operation_uuid": (
                str(self.unlinked_operation.uuid) if self.unlinked_operation else None
            ),
            "transferred_by": self.created_by.username if self.created_by else None,
            "transferred_at": self.created_at,
        }
