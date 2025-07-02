from fastapi import Depends, HTTPException, status, Cookie, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from typing import Optional, Union
from datetime import datetime
import logging

# Imports internos
from app.database.connection import get_db
from app.repositories.user_repository import UserRepository
from app.core.security import decode_access_token, verify_token
from app.core.config import settings
from app.core.auth_config import auth_config
from app.models.user import User
from app.enums.user_roles import UserRole

# Configurar logging
logger = logging.getLogger(__name__)

# Configurar esquema de autenticación Bearer
security = HTTPBearer(auto_error=False)  # auto_error=False para manejar cookies también

class AuthenticationError(HTTPException):
    """Excepción personalizada para errores de autenticación."""
    def __init__(self, detail: str = "Could not validate credentials"):
        super().__init__(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

class AuthorizationError(HTTPException):
    """Excepción personalizada para errores de autorización."""
    def __init__(self, detail: str = "Insufficient permissions"):
        super().__init__(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail
        )

async def get_token_from_request(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    access_token: Optional[str] = Cookie(None, alias=auth_config.COOKIE_NAME)
) -> Optional[str]:
    """
    Extraer token de la request, ya sea del header Authorization o de cookies.
    Prioriza el header sobre las cookies.
    """
    
    # 1. Intentar obtener del header Authorization
    if credentials and credentials.credentials:
        logger.debug("Token found in Authorization header")
        return credentials.credentials
    
    # 2. Intentar obtener de cookies
    if access_token:
        logger.debug("Token found in cookie")
        return access_token
    
    # 3. Intentar obtener de query params (útil para websockets o casos especiales)
    query_token = request.query_params.get("token")
    if query_token:
        logger.debug("Token found in query params")
        return query_token
    
    logger.debug("No token found in request")
    return None

async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(get_token_from_request)
) -> User:
    """
    Obtener el usuario actual desde el token JWT.
    Maneja tanto tokens en headers como en cookies.
    """
    
    if not token:
        logger.warning("Authentication failed: No token provided")
        raise AuthenticationError("No authentication token provided")
    
    try:
        # Decodificar el token
        payload = decode_access_token(token)
        
        # Extraer identificador del usuario (puede ser email o ID)
        user_identifier: Union[str, int] = payload.get("sub")
        if user_identifier is None:
            logger.warning("Authentication failed: No subject in token")
            raise AuthenticationError("Invalid token: missing subject")
        
        # Buscar el usuario en la base de datos
        user_repo = UserRepository(db)
        
        # Intentar primero por email (más común), luego por ID
        if isinstance(user_identifier, str) and "@" in user_identifier:
            user = user_repo.get_by_email(user_identifier)
        else:
            # Intentar convertir a int si es un ID
            try:
                user_id = int(user_identifier)
                user = user_repo.get_by_id(user_id)
            except (ValueError, TypeError):
                # Si no se puede convertir, intentar como email
                user = user_repo.get_by_email(str(user_identifier))
        
        if user is None:
            logger.warning(f"Authentication failed: User not found for identifier: {user_identifier}")
            raise AuthenticationError("User not found")
        
        # Verificar que el usuario esté autenticado (si tienes este campo)
        if hasattr(user, 'is_authenticated') and not user.is_authenticated:
            logger.warning(f"Authentication failed: User {user.id} not authenticated")
            raise AuthenticationError("User account not authenticated")
        
        # Actualizar último acceso (opcional)
        if settings.is_production:  # Solo en producción para evitar spam en desarrollo
            user_repo.update_last_login(user.id)
        
        logger.debug(f"Authentication successful for user {user.id}")
        return user
        
    except HTTPException:
        # Re-lanzar excepciones HTTP existentes
        raise
    except Exception as e:
        logger.error(f"Authentication failed with unexpected error: {str(e)}")
        raise AuthenticationError("Authentication failed")

async def get_current_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """
    Obtener el usuario actual y verificar que esté activo.
    """
    
    # Verificar si el usuario está verificado
    if hasattr(current_user, 'is_verified') and not current_user.is_verified:
        logger.warning(f"Access denied: User {current_user.id} is not verified")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address not verified"
        )
    
    # Verificar si el usuario está activo
    if hasattr(current_user, 'is_active') and not current_user.is_active:
        logger.warning(f"Access denied: User {current_user.id} is inactive")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User account is inactive"
        )
    
    # Verificar si la cuenta está bloqueada
    if hasattr(current_user, 'locked_until') and current_user.locked_until:
        if current_user.locked_until > datetime.utcnow():
            logger.warning(f"Access denied: User {current_user.id} is locked until {current_user.locked_until}")
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail=f"Account is locked until {current_user.locked_until.isoformat()}"
            )
    
    return current_user

async def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(get_token_from_request)
) -> Optional[User]:
    """
    Obtener el usuario actual si está autenticado, pero no fallar si no lo está.
    Útil para endpoints que funcionan tanto para usuarios autenticados como anónimos.
    """
    if not token:
        return None
    
    try:
        return await get_current_user(request, db, token)
    except HTTPException:
        return None

def require_permission(resource: str, action: str):
    """
    Decorador para requerir un permiso específico.
    """
    async def permission_dependency(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if not hasattr(current_user, 'has_permission'):
            logger.error(f"User model doesn't have has_permission method")
            raise AuthorizationError("Permission system not available")
        
        if not current_user.has_permission(resource, action):
            logger.warning(
                f"Permission denied: User {current_user.id} lacks permission "
                f"'{action}' on resource '{resource}'"
            )
            raise AuthorizationError(
                f"Insufficient permissions: {action} on {resource}"
            )
        
        logger.debug(f"Permission granted: User {current_user.id} can {action} on {resource}")
        return current_user
    
    return permission_dependency

def require_role(min_role: UserRole):
    """
    Decorador para requerir un rol mínimo.
    """
    async def role_dependency(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if not current_user.role:
            logger.warning(f"Access denied: User {current_user.id} has no role assigned")
            raise AuthorizationError("No role assigned to user")
        
        # Verificar nivel de rol (asumiendo que UserRole tiene un atributo 'value' o similar)
        user_role_level = getattr(current_user.role, 'value', 0)
        required_role_level = getattr(min_role, 'value', 999)
        
        if user_role_level < required_role_level:
            logger.warning(
                f"Access denied: User {current_user.id} has role {current_user.role.name} "
                f"but requires {min_role.name}"
            )
            raise AuthorizationError(
                f"Insufficient role: requires {min_role.name} or higher"
            )
        
        logger.debug(f"Role check passed: User {current_user.id} has role {current_user.role.name}")
        return current_user
    
    return role_dependency

def require_any_role(*roles: UserRole):
    """
    Decorador para requerir cualquiera de los roles especificados.
    """
    async def role_dependency(
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        if not current_user.role:
            logger.warning(f"Access denied: User {current_user.id} has no role assigned")
            raise AuthorizationError("No role assigned to user")
        
        if current_user.role not in roles:
            role_names = [role.name for role in roles]
            logger.warning(
                f"Access denied: User {current_user.id} has role {current_user.role.name} "
                f"but requires one of: {role_names}"
            )
            raise AuthorizationError(
                f"Insufficient role: requires one of {role_names}"
            )
        
        return current_user
    
    return role_dependency

async def get_user_user(
    current_user: User = Depends(require_role(UserRole.USER))
) -> User:
    """Dependencia para usuarios con rol USER o superior."""
    return current_user

async def get_moderator_user(
    current_user: User = Depends(require_role(UserRole.MODERATOR))
) -> User:
    """Dependencia para usuarios con rol MODERATOR o superior."""
    return current_user

async def get_root_user(
    current_user: User = Depends(require_role(UserRole.ROOT))
) -> User:
    """Dependencia para usuarios con rol ROOT."""
    return current_user

async def get_admin_user(
    current_user: User = Depends(require_any_role(UserRole.MODERATOR, UserRole.ROOT))
) -> User:
    """Dependencia para usuarios con rol administrativo (MODERATOR o ROOT)."""
    return current_user

async def get_current_user_id(
    current_user: User = Depends(get_current_user)
) -> int:
    """Obtener solo el ID del usuario actual."""
    return current_user.id

async def get_current_user_email(
    current_user: User = Depends(get_current_user)
) -> str:
    """Obtener solo el email del usuario actual."""
    return current_user.email

def require_self_or_admin(user_id_param: str = "user_id"):
    """
    Dependencia que permite acceso si el usuario es el propietario del recurso o es admin.
    
    Args:
        user_id_param: Nombre del parámetro de path que contiene el user_id
    """
    async def self_or_admin_dependency(
        request: Request,
        current_user: User = Depends(get_current_active_user)
    ) -> User:
        # Obtener el user_id del path parameter
        target_user_id = request.path_params.get(user_id_param)
        
        if not target_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing {user_id_param} parameter"
            )
        
        try:
            target_user_id = int(target_user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid {user_id_param} format"
            )
        
        # Verificar si es el mismo usuario o es admin
        is_self = current_user.id == target_user_id
        is_admin = current_user.role in [UserRole.MODERATOR, UserRole.ROOT]
        
        if not (is_self or is_admin):
            logger.warning(
                f"Access denied: User {current_user.id} tried to access "
                f"resource for user {target_user_id}"
            )
            raise AuthorizationError(
                "You can only access your own resources or must be an administrator"
            )
        
        return current_user
    
    return self_or_admin_dependency

# =================
# UTILIDADES DE RATE LIMITING (si implementas en el futuro)
# =================

async def check_rate_limit(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_user)
) -> None:
    """
    Placeholder para implementación futura de rate limiting.
    """
    # TODO: Implementar rate limiting basado en usuario o IP
    pass