from datetime import timedelta
from app.core.config import settings

class AuthConfig:
    """Configuración específica de autenticación que usa el settings principal."""
    
    @property
    def SECRET_KEY(self) -> str:
        return settings.JWT_SECRET_KEY
    
    @property
    def ALGORITHM(self) -> str:
        return settings.JWT_ALGORITHM
    
    @property
    def ACCESS_TOKEN_EXPIRE_MINUTES(self) -> int:
        return settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
    
    @property
    def REFRESH_TOKEN_EXPIRE_DAYS(self) -> int:
        return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS
    
    # Cookie Configuration
    COOKIE_NAME: str = "access_token"
    COOKIE_HTTPONLY: bool = True
    COOKIE_SAMESITE: str = "lax"
    
    @property
    def COOKIE_SECURE(self) -> bool:
        return settings.is_production
    
    @property
    def COOKIE_MAX_AGE(self) -> int:
        return self.ACCESS_TOKEN_EXPIRE_MINUTES * 60
    
    # Password Configuration
    PWD_CONTEXT_SCHEMES: list[str] = ["bcrypt"]
    PWD_CONTEXT_DEPRECATED: str = "auto"
    
    def get_access_token_expire_delta(self) -> timedelta:
        """Obtiene el delta de expiración para tokens de acceso."""
        return timedelta(minutes=self.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    def get_refresh_token_expire_delta(self) -> timedelta:
        """Obtiene el delta de expiración para tokens de refresh."""
        return timedelta(days=self.REFRESH_TOKEN_EXPIRE_DAYS)
    
    def get_lockout_expire_delta(self) -> timedelta:
        """Obtiene el delta de expiración para bloqueo de cuenta."""
        return timedelta(minutes=settings.LOCKOUT_DURATION_MINUTES)

# Instancia global
auth_config = AuthConfig()