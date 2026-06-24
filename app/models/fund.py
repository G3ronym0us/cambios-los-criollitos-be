import enum
import uuid as _uuid
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Enum as SQLEnum, UniqueConstraint
from sqlalchemy.types import TypeDecorator
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.models.mixins import UUIDMixin


class FundMovementType(enum.Enum):
    DEPOSIT    = "DEPOSIT"     # Gestor deposita USD al fondo (Binance/Kraken → Zelle)
    EXCHANGE   = "EXCHANGE"    # Cambio gestionado: gestor envía USD al cliente, sale del fondo
    PERSONAL   = "PERSONAL"    # Gasto personal del gestor con fondos del fondo (queda como deuda)
    ADJUSTMENT = "ADJUSTMENT"  # Corrección manual


class FundDepositMethod(enum.Enum):
    """Origen/método de un depósito al fondo. Se guarda como string en fund_movements.deposit_method."""
    ZELLE    = "ZELLE"
    BINANCE  = "BINANCE"
    KRAKEN   = "KRAKEN"
    TRANSFER = "TRANSFER"
    OTHER    = "OTHER"


class CaseInsensitiveEnum(TypeDecorator):
    """Enum respaldado por VARCHAR, tolerante a mayúsc/minúsc al leer y que normaliza a
    MAYÚSCULA al escribir. La columna `fund_movements.movement_type` ya es varchar y tuvo
    data legacy en minúscula ('deposit'); con esto una sola fila con caso raro no vuelve a
    tumbar la query entera con LookupError, y las escrituras quedan consistentes en mayúscula.
    """
    impl = String(20)
    cache_ok = True

    def __init__(self, enum_cls, *args, **kwargs):
        self._enum_cls = enum_cls
        super().__init__(*args, **kwargs)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, self._enum_cls):
            return value.value.upper()
        return str(value).upper()

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self._enum_cls(value.upper())


class FundGroup(UUIDMixin, Base):
    """
    Grupo de gestores que comparten un fondo y consolidan su ganancia en reportes.
    Ejemplo: grupo "Zelle/Paypal" con Jean + Diohandres, moneda base USD.
    """
    __tablename__ = "fund_groups"

    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(_uuid.uuid4()), index=True)
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    currency = Column(String(10), nullable=False)  # Moneda base del fondo: "USD", "COP"
    # JID del grupo de WhatsApp (...@g.us) asociado a este fondo. Permite que el backend
    # resuelva el FundGroup a partir del JID que el bot manda al reenviar capturas.
    whatsapp_group_jid = Column(String(64), nullable=True, index=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relaciones
    members = relationship("FundGroupMember", back_populates="group", cascade="all, delete-orphan")
    movements = relationship("FundMovement", back_populates="group")

    def __repr__(self):
        return f"<FundGroup(id={self.id}, name={self.name}, currency={self.currency})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "name": self.name,
            "currency": self.currency,
            "whatsapp_group_jid": self.whatsapp_group_jid,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class FundGroupMember(UUIDMixin, Base):
    """
    Membresía de un gestor en un grupo de fondo.
    is_fund_manager=True indica que gestiona las cuentas físicas (ej: Jean con Zelle).
    """
    __tablename__ = "fund_group_members"

    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(_uuid.uuid4()), index=True)
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("fund_groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    is_fund_manager = Column(Boolean, default=False, nullable=False)
    # Número de WhatsApp del socio. Si está seteado (y is_fund_manager), el bot detecta
    # los mensajes de este número como entrantes reportados por el socio (escenario VIA_PARTNER).
    whatsapp_phone = Column(String(32), nullable=True, index=True)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relaciones
    group = relationship("FundGroup", back_populates="members")
    user = relationship("User")

    __table_args__ = (
        UniqueConstraint("group_id", "user_id", name="unique_fund_group_member"),
    )

    @property
    def user_uuid(self):
        return self.user.uuid if self.user else None

    @property
    def username(self):
        return self.user.username if self.user else None

    def __repr__(self):
        return f"<FundGroupMember(group_id={self.group_id}, user_id={self.user_id}, manager={self.is_fund_manager})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "group_uuid": self.group.uuid if self.group else None,
            "user_uuid": self.user.uuid if self.user else None,
            "username": self.user.username if self.user else None,
            "is_fund_manager": self.is_fund_manager,
            "whatsapp_phone": self.whatsapp_phone,
            "joined_at": self.joined_at,
        }


class FundMovement(UUIDMixin, Base):
    """
    Registro de cada movimiento que afecta la posición de un gestor dentro de un fondo.

    Tipos:
    - DEPOSIT:    gestor deposita → aumenta posición (fondo debe al gestor)
    - EXCHANGE:   cambio realizado → disminuye posición (gestor usó fondos)
    - PERSONAL:   gasto personal → disminuye posición (gestor debe al fondo)
    - ADJUSTMENT: corrección manual
    """
    __tablename__ = "fund_movements"

    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(_uuid.uuid4()), index=True)
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("fund_groups.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    movement_type = Column(CaseInsensitiveEnum(FundMovementType), nullable=False, index=True)
    amount = Column(Float, nullable=False)          # Siempre positivo; el tipo determina signo
    currency = Column(String(10), nullable=False)

    amount_usdt = Column(Float, nullable=True)      # Equivalente USDT al momento del movimiento
    usdt_rate = Column(Float, nullable=True)        # Tasa usada para la conversión

    # Vínculo opcional con una transacción (para movimientos tipo EXCHANGE)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=True)

    reference = Column(String(200), nullable=True)  # Hash Binance/Kraken, ID externo, etc.
    notes = Column(Text, nullable=True)

    # Solo para DEPOSIT: método/origen (ZELLE/BINANCE/KRAKEN/TRANSFER/OTHER) y el pago entrante
    # (comprobante OCR) desde el que se registró el depósito.
    deposit_method = Column(String(20), nullable=True)
    incoming_payment_id = Column(
        Integer,
        ForeignKey("whatsapp_incoming_payments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    recorded_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    movement_date = Column(DateTime(timezone=True), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relaciones
    group = relationship("FundGroup", back_populates="movements")
    user = relationship("User", foreign_keys=[user_id])
    transaction = relationship("Transaction")
    recorded_by = relationship("User", foreign_keys=[recorded_by_user_id])

    def __repr__(self):
        return f"<FundMovement(id={self.id}, type={self.movement_type.value}, amount={self.amount} {self.currency})>"

    def dict(self):
        return {
            "uuid": self.uuid,
            "group_uuid": self.group.uuid if self.group else None,
            "group_name": self.group.name if self.group else None,
            "user_uuid": self.user.uuid if self.user else None,
            "username": self.user.username if self.user else None,
            "movement_type": self.movement_type.value if self.movement_type else None,
            "amount": self.amount,
            "currency": self.currency,
            "amount_usdt": self.amount_usdt,
            "usdt_rate": self.usdt_rate,
            "transaction_uuid": self.transaction.uuid if self.transaction else None,
            "reference": self.reference,
            "notes": self.notes,
            "deposit_method": self.deposit_method,
            "incoming_payment_id": self.incoming_payment_id,
            "recorded_by_uuid": self.recorded_by.uuid if self.recorded_by else None,
            "recorded_by_username": self.recorded_by.username if self.recorded_by else None,
            "movement_date": self.movement_date,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class FundPendingDepositStatus(enum.Enum):
    PENDING   = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED  = "REJECTED"


class FundPendingDeposit(UUIDMixin, Base):
    """
    Depósito DETECTADO por el bot cuando un gestor (FundGroupMember.is_fund_manager) sube un
    comprobante al grupo. Queda PENDING hasta que un operador lo confirma/rechaza desde
    `/admin/funds`. Al confirmar se crea un FundMovement DEPOSIT (`confirmed_movement_id`).
    Tabla separada para no ensuciar el ledger (el balance solo cuenta FundMovement).
    """
    __tablename__ = "fund_pending_deposits"

    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(_uuid.uuid4()), index=True)
    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("fund_groups.id", ondelete="CASCADE"), nullable=False, index=True)
    # Gestor que subió el comprobante (autor del mensaje en el grupo). NULL si no se resolvió.
    detected_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)

    amount = Column(Float, nullable=True)
    currency = Column(String(10), nullable=True)
    provider = Column(String(60), nullable=True)
    reference = Column(String(120), nullable=True)
    raw_text = Column(Text, nullable=True)

    status = Column(
        SQLEnum(FundPendingDepositStatus),
        nullable=False,
        server_default=FundPendingDepositStatus.PENDING.value,
        index=True,
    )
    confirmed_movement_id = Column(
        Integer, ForeignKey("fund_movements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    resolved_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    group = relationship("FundGroup", foreign_keys=[group_id])
    detected_user = relationship("User", foreign_keys=[detected_user_id])
    confirmed_movement = relationship("FundMovement", foreign_keys=[confirmed_movement_id])

    def dict(self):
        return {
            "uuid": self.uuid,
            "group_uuid": self.group.uuid if self.group else None,
            "group_name": self.group.name if self.group else None,
            "detected_user_uuid": self.detected_user.uuid if self.detected_user else None,
            "detected_username": self.detected_user.username if self.detected_user else None,
            "amount": self.amount,
            "currency": self.currency,
            "provider": self.provider,
            "reference": self.reference,
            "raw_text": self.raw_text,
            "status": self.status.value if self.status else None,
            "confirmed_movement_uuid": self.confirmed_movement.uuid if self.confirmed_movement else None,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
