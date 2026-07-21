from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class WhatsAppClient(UUIDMixin, Base):
    """
    Cliente del bot de WhatsApp (identificado por teléfono).

    Reemplaza las tablas SQLite del bot: clients, client_preferences,
    tracked_clients, blocked_clients, client_flags.

    No es un User del sistema; los Users son operadores/socios. Si en el
    futuro un operador es también cliente, se vincula con linked_user_id.
    """
    __tablename__ = "whatsapp_clients"

    id = Column(Integer, primary_key=True, index=True)

    # Identificador principal: número de teléfono (sin sufijos WhatsApp)
    phone = Column(String(32), unique=True, nullable=False, index=True)
    display_name = Column(String(120), nullable=True)

    # Par por defecto del cliente (reemplaza client_preferences)
    preferred_pair_id = Column(Integer, ForeignKey("currency_pairs.id"), nullable=True)

    # Estados (reemplazan tracked_clients / blocked_clients / client_flags)
    is_tracked = Column(Boolean, default=False, nullable=False)
    is_blocked = Column(Boolean, default=False, nullable=False)
    is_usdt_authorized = Column(Boolean, default=False, nullable=False)

    # Cuenta de pago predeterminada del cliente (una sola). `default_payment_info`
    # es el bloque de datos (banco/cédula/teléfono, cuenta, o llave Pix) en texto;
    # el bot lo re-normaliza al inyectarlo en una cotización sin datos. La moneda
    # fiat indica en qué cotizaciones aplica (VES, BRL, COP...).
    default_payment_info = Column(Text, nullable=True)
    default_payment_currency = Column(String(10), nullable=True)

    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    preferred_pair = relationship("CurrencyPair", foreign_keys=[preferred_pair_id])
    operations = relationship("WhatsAppOperation", back_populates="client")

    def __repr__(self):
        return f"<WhatsAppClient(phone={self.phone}, name={self.display_name})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "phone": self.phone,
            "display_name": self.display_name,
            "preferred_pair_uuid": self.preferred_pair.uuid if self.preferred_pair else None,
            "preferred_pair_symbol": self.preferred_pair.pair_symbol if self.preferred_pair else None,
            "is_tracked": self.is_tracked,
            "is_blocked": self.is_blocked,
            "is_usdt_authorized": self.is_usdt_authorized,
            "default_payment_info": self.default_payment_info,
            "default_payment_currency": self.default_payment_currency,
            "last_seen_at": self.last_seen_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
