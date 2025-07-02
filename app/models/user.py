from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, Enum as SQLEnum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database.connection import Base
from app.enums.user_roles import UserRole

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    
    # Estados del usuario
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    
    # Rol del usuario usando Enum directamente
    role = Column(SQLEnum(UserRole), nullable=False, default=UserRole.USER)
    
    # Campos de autenticación
    last_login = Column(DateTime(timezone=True), nullable=True)
    login_count = Column(Integer, default=0)
    failed_login_attempts = Column(Integer, default=0)
    locked_until = Column(DateTime(timezone=True), nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Campos adicionales
    avatar_url = Column(String, nullable=True)
    bio = Column(Text, nullable=True)
    phone_number = Column(String, nullable=True)
    
    # Relación con Transaction
    transactions = relationship("Transaction", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, username={self.username}, role={self.role.value if self.role else 'None'})>"

    @property
    def is_authenticated(self) -> bool:
        """Usuario está autenticado si está activo y no bloqueado"""
        from datetime import datetime
        if not self.is_active:
            return False
        if self.locked_until and self.locked_until > datetime.utcnow():
            return False
        return True

    @property
    def is_admin(self) -> bool:
        """Verificar si es administrador (moderator o root)"""
        return self.role and self.role.level >= 2

    @property
    def is_root(self) -> bool:
        """Verificar si es root"""
        return self.role == UserRole.ROOT

    @property
    def is_moderator(self) -> bool:
        """Verificar si es moderator"""
        return self.role == UserRole.MODERATOR

    def has_permission(self, resource: str, action: str) -> bool:
        """Verificar si tiene un permiso específico"""
        if not self.role:
            return False
        
        # Definir permisos por rol
        permissions = self._get_role_permissions()
        return f"{resource}:{action}" in permissions.get(self.role, [])

    def can_manage_user(self, other_user: 'User') -> bool:
        """Verificar si puede gestionar otro usuario"""
        if not self.role or not other_user.role:
            return False
        return self.role.can_manage(other_user.role)

    def _get_role_permissions(self) -> dict:
        """Obtener permisos por rol"""
        return {
            UserRole.USER: [
                "rates:read",
                "transactions:read", 
                "transactions:create"
            ],
            UserRole.MODERATOR: [
                "rates:read", "rates:create", "rates:update", "rates:scrape",
                "transactions:read", "transactions:create", "transactions:update",
                "users:read", "users:update",
                "system:logs"
            ],
            UserRole.ROOT: [
                # Todos los permisos
                "users:read", "users:create", "users:update", "users:delete", "users:manage",
                "rates:read", "rates:create", "rates:update", "rates:delete", "rates:scrape",
                "transactions:read", "transactions:create", "transactions:update", "transactions:delete",
                "system:admin", "system:logs", "system:backup"
            ]
        }

    def dict(self):
        """Convertir a diccionario para respuestas JSON"""
        permissions = self._get_role_permissions()
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "role": self.role.value if self.role else None,
            "role_display": self.role.value.title() if self.role else None,
            "permissions": permissions.get(self.role, []),
            "last_login": self.last_login,
            "created_at": self.created_at,
            "avatar_url": self.avatar_url,
            "bio": self.bio,
            "phone_number": self.phone_number
        }
