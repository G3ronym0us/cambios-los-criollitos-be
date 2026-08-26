import json
import os
from dataclasses import dataclass
from typing import Optional, List, Union
from pydantic_settings import BaseSettings
from pydantic import validator, Field


@dataclass(frozen=True)
class MailboxConfig:
    """Un buzón de una cuenta alquilada. `label` es lo que ve el operador en el aviso."""

    label: str
    email: str
    password: str


def parse_mailboxes(raw: Optional[str]) -> list["MailboxConfig"]:
    """
    Parsea ZELLE_MAILBOXES. Vacío = feature apagada (no rompe el arranque).

    Se valida acá y no al usarlo: un typo en el .env tiene que fallar fuerte y claro,
    no convertirse en confirmaciones que nunca llegan.
    """
    if not raw or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(f"ZELLE_MAILBOXES no es JSON válido: {e}") from e

    if not isinstance(data, list):
        raise ValueError("ZELLE_MAILBOXES debe ser una lista de objetos")

    boxes: list[MailboxConfig] = []
    for i, item in enumerate(data):
        for field in ("label", "email", "password"):
            if not isinstance(item, dict) or not item.get(field):
                raise ValueError(f"ZELLE_MAILBOXES[{i}]: falta el campo '{field}'")
        boxes.append(
            MailboxConfig(label=item["label"], email=item["email"], password=item["password"])
        )
    return boxes


class Settings(BaseSettings):
    """Configuración principal de la aplicación."""
    
    # =================
    # BASE DE DATOS
    # =================
    DATABASE_URL: str
    DATABASE_ECHO: bool = False  # Para debug de SQL queries
    
    # =================
    # REDIS
    # =================
    REDIS_URL: str = "redis://localhost:6380/0"
    REDIS_DECODE_RESPONSES: bool = True
    REDIS_MAX_CONNECTIONS: int = 20
    
    # =================
    # APLICACIÓN
    # =================
    APP_NAME: str = "Tasas Project API"
    APP_VERSION: str = "1.0.0"
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    # El eco de SQL va aparte de DEBUG y por defecto apagado. Cuando colgaba de DEBUG —cuyo
    # default es True— bastaba un .env sin la variable para que producción registrara cada
    # sentencia: el json-file del backend llegó a 427 MB y llenó el disco del EC2 hasta hacer
    # fallar el build del deploy. Encenderlo tiene que ser una decisión, no un descuido.
    SQL_ECHO: bool = False
    
    # CORS - CORREGIDO: Usar Union para permitir string o lista
    CORS_ORIGINS: Union[str, List[str]] = "http://localhost:3000,http://localhost:8080"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: List[str] = ["*"]
    CORS_ALLOW_HEADERS: List[str] = ["*"]
    
    # =================
    # SEGURIDAD Y JWT
    # =================
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    
    # Configuración de passwords
    PASSWORD_MIN_LENGTH: int = 8
    PASSWORD_REQUIRE_UPPERCASE: bool = True
    PASSWORD_REQUIRE_LOWERCASE: bool = True
    PASSWORD_REQUIRE_NUMBERS: bool = True
    PASSWORD_REQUIRE_SPECIAL: bool = False  # Más flexible por defecto
    BCRYPT_ROUNDS: int = 12
    
    # Rate limiting para login
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_DURATION_MINUTES: int = 15
    
    # Usuario Root (para script CLI)
    ROOT_USER_EMAIL: Optional[str] = None
    ROOT_USER_PASSWORD: Optional[str] = None
    ROOT_USER_NAME: str = "System Administrator"
    
    # =================
    # ALERTAS DE DIVERGENCIA DE TASAS
    # =================
    RATE_DIVERGENCE_THRESHOLD: float = 1.0  # % mínimo para generar alerta

    # =================
    # SCRAPING
    # =================
    SCRAPING_INTERVAL_MINUTES: int = 5
    SCRAPING_ENABLED: bool = True
    SCRAPING_TIMEOUT_SECONDS: int = 30
    SCRAPING_MAX_RETRIES: int = 3
    SCRAPING_USER_AGENT: str = "TasasProject-Scraper/1.0"
    
    # URLs de fuentes de datos
    BINANCE_API_URL: str = "https://api.binance.com/api/v3"
    COINDESK_API_URL: str = "https://api.coindesk.com/v1"
    
    # =================
    # EMAIL (OPCIONAL)
    # =================
    SMTP_SERVER: Optional[str] = None
    SMTP_PORT: Optional[int] = None
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_USE_TLS: bool = True
    EMAIL_FROM: Optional[str] = None
    EMAIL_FROM_NAME: Optional[str] = None
    
    # =================
    # CELERY (PARA TAREAS ASÍNCRONAS)
    # =================
    CELERY_BROKER_URL: Optional[str] = None  # Usará REDIS_URL si no se especifica
    CELERY_RESULT_BACKEND: Optional[str] = None  # Usará REDIS_URL si no se especifica
    CELERY_TASK_ALWAYS_EAGER: bool = False  # True para testing
    
    # =================
    # ARCHIVOS Y UPLOADS
    # =================
    MAX_FILE_SIZE_MB: int = 10
    ALLOWED_FILE_EXTENSIONS: List[str] = [".jpg", ".jpeg", ".png", ".pdf"]
    UPLOAD_DIR: str = "uploads"
    
    # =================
    # CACHE
    # =================
    CACHE_TTL_SECONDS: int = 300  # 5 minutos
    CACHE_ENABLED: bool = True

    # =================
    # BOT DE WHATSAPP (Service account)
    # =================
    BOT_API_KEY: Optional[str] = None              # Token compartido bot ↔ backend
    BOT_SERVICE_USER_EMAIL: Optional[str] = None   # User existente que el bot "encarna"
    BOT_NOTIFY_URL: Optional[str] = None           # Base URL del dashboard del bot (ej. http://localhost:3457) para avisos al operador

    # =================
    # BUZONES DE LAS CUENTAS ALQUILADAS (confirmación de Zelle por correo)
    # =================
    # JSON: [{"label":"Jean","email":"...@gmail.com","password":"<app password de Gmail>"}]
    # Vacío = confirmación por correo apagada.
    ZELLE_MAILBOXES: Optional[str] = None

    # =================
    # ACTUALIZACIÓN EXTERNA DE TASAS (script del operador)
    # =================
    # Para monedas cuyo P2P no se puede leer desde el servidor (ej. BRL: Binance dejó
    # de exponer anuncios USDT/BRL en su API pública global). Un script corre en la
    # máquina del operador (en el país) y empuja la tasa con X-API-Key.
    EXTERNAL_RATE_API_KEY: Optional[str] = None    # Token compartido script ↔ backend

    # =================
    # WEB PUSH (VAPID)
    # =================
    VAPID_PRIVATE_KEY: Optional[str] = None        # Clave privada raw base64url (py_vapid)
    VAPID_PUBLIC_KEY: Optional[str] = None         # Clave pública uncompressed point base64url
    VAPID_CLAIMS_SUB: str = "mailto:admin@example.com"

    # =================
    # COOKIE
    # =================
    COOKIE_NAME: str = "access_token"
    COOKIE_HTTPONLY: bool = True
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    
    # =================
    # VALIDADORES
    # =================
    @validator('DATABASE_URL')
    def validate_database_url(cls, v):
        if not v:
            raise ValueError('DATABASE_URL is required')
        if not v.startswith('postgresql://'):
            raise ValueError('DATABASE_URL must be a PostgreSQL URL')
        return v
    
    @validator('JWT_SECRET_KEY')
    def validate_jwt_secret(cls, v):
        if not v or len(v) < 32:
            raise ValueError('JWT_SECRET_KEY must be at least 32 characters long')
        return v
    
    @validator('APP_ENV')
    def validate_app_env(cls, v):
        allowed_envs = ['development', 'testing', 'staging', 'production']
        if v.lower() not in allowed_envs:
            raise ValueError(f'APP_ENV must be one of: {allowed_envs}')
        return v.lower()
    
    @validator('CORS_ORIGINS')
    def validate_cors_origins(cls, v):
        """Convertir string separado por comas a lista."""
        if isinstance(v, str):
            # Dividir por comas y limpiar espacios
            origins = [origin.strip() for origin in v.split(',') if origin.strip()]
            return origins
        elif isinstance(v, list):
            # Ya es una lista, solo limpiar
            return [origin.strip() for origin in v if origin.strip()]
        else:
            raise ValueError('CORS_ORIGINS must be a string or list')

    @validator('ZELLE_MAILBOXES', pre=True)
    def keep_mailboxes_as_json(cls, v):
        """
        Devolver el valor como TEXTO, aunque pydantic ya lo haya parseado.

        pydantic-settings parsea solo cualquier valor del .env que parezca JSON antes de
        validar; el `json_loads` de la clase Config es de pydantic v1 y v2 lo ignora (de
        ahí el UserWarning al arrancar). Sin esto, `ZELLE_MAILBOXES` llega como lista y
        revienta contra el tipo `str`. Se vuelve a serializar para que `parse_mailboxes`
        reciba siempre texto y sea el único lugar que valida los campos.
        """
        if v is None or isinstance(v, str):
            return v
        return json.dumps(v)

    # =================
    # PROPIEDADES CALCULADAS
    # =================
    @property
    def is_production(self) -> bool:
        """Verifica si estamos en producción."""
        return self.APP_ENV == "production"
    
    @property
    def is_development(self) -> bool:
        """Verifica si estamos en desarrollo."""
        return self.APP_ENV == "development"
    
    @property
    def is_testing(self) -> bool:
        """Verifica si estamos en testing."""
        return self.APP_ENV == "testing"
    
    @property
    def celery_broker_url_computed(self) -> str:
        """URL del broker de Celery (usa Redis por defecto)."""
        return self.CELERY_BROKER_URL or self.REDIS_URL

    @property
    def mailboxes_computed(self) -> list[MailboxConfig]:
        """Buzones de las cuentas alquiladas ya parseados (lista vacía = feature apagada)."""
        return parse_mailboxes(self.ZELLE_MAILBOXES)

    @property
    def celery_result_backend_computed(self) -> str:
        """Backend de resultados de Celery (usa Redis por defecto)."""
        return self.CELERY_RESULT_BACKEND or self.REDIS_URL
    
    @property
    def database_echo_computed(self) -> bool:
        """Echo de base de datos basado en el entorno."""
        return self.DATABASE_ECHO and self.is_development
    
    @property
    def cors_origins_list(self) -> List[str]:
        """Obtener CORS origins como lista (por compatibilidad)."""
        if isinstance(self.CORS_ORIGINS, str):
            return [origin.strip() for origin in self.CORS_ORIGINS.split(',') if origin.strip()]
        return self.CORS_ORIGINS or []
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True
        # Permitir que las variables de entorno sobrescriban los valores
        # IMPORTANTE: Deshabilitar el parsing automático de JSON para evitar conflictos
        json_loads = lambda x: x  # No parsear como JSON automáticamente

# Instancia global de configuración
settings = Settings()