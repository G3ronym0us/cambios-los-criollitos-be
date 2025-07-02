from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import HTTPException, status
import re
from app.core.config import settings
from app.core.auth_config import auth_config

# Configuración para el hashing de contraseñas
pwd_context = CryptContext(
    schemes=auth_config.PWD_CONTEXT_SCHEMES,
    deprecated=auth_config.PWD_CONTEXT_DEPRECATED,
    bcrypt__rounds=settings.BCRYPT_ROUNDS
)

def validate_password_strength(password: str) -> tuple[bool, list[str]]:
    """Validar la fortaleza de una contraseña usando configuración global."""
    errors = []
    
    if len(password) < settings.PASSWORD_MIN_LENGTH:
        errors.append(f"Password must be at least {settings.PASSWORD_MIN_LENGTH} characters long")
    
    if settings.PASSWORD_REQUIRE_UPPERCASE and not re.search(r'[A-Z]', password):
        errors.append("Password must contain at least one uppercase letter")
    
    if settings.PASSWORD_REQUIRE_LOWERCASE and not re.search(r'[a-z]', password):
        errors.append("Password must contain at least one lowercase letter")
    
    if settings.PASSWORD_REQUIRE_NUMBERS and not re.search(r'\d', password):
        errors.append("Password must contain at least one number")
    
    if settings.PASSWORD_REQUIRE_SPECIAL and not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        errors.append("Password must contain at least one special character")
    
    return len(errors) == 0, errors

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Crear token de acceso JWT."""
    to_encode = data.copy()
    
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + auth_config.get_access_token_expire_delta()
    
    to_encode.update({
        "exp": expire,
        "type": "access",
        "iat": datetime.utcnow()
    })
    
    return jwt.encode(to_encode, auth_config.SECRET_KEY, algorithm=auth_config.ALGORITHM)

def create_refresh_token(data: dict) -> str:
    """Crear token de refresh JWT."""
    to_encode = data.copy()
    expire = datetime.utcnow() + auth_config.get_refresh_token_expire_delta()
    
    to_encode.update({
        "exp": expire,
        "type": "refresh",
        "iat": datetime.utcnow()
    })
    
    return jwt.encode(to_encode, auth_config.SECRET_KEY, algorithm=auth_config.ALGORITHM)

def verify_token(token: str, expected_type: str = None) -> Optional[dict]:
    """Verificar y decodificar token JWT."""
    try:
        payload = jwt.decode(
            token, 
            auth_config.SECRET_KEY, 
            algorithms=[auth_config.ALGORITHM]
        )
        
        # Verificar tipo de token si se especifica
        if expected_type and payload.get("type") != expected_type:
            return None
            
        return payload
    except JWTError:
        return None

def decode_access_token(token: str) -> dict:
    """Decodificar token de acceso y validar que sea del tipo correcto."""
    payload = verify_token(token, "access")
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return payload

def decode_refresh_token(token: str) -> dict:
    """Decodificar token de refresh y validar que sea del tipo correcto."""
    payload = verify_token(token, "refresh")
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate refresh token"
        )
    
    return payload

def decode_email_verification_token(token: str) -> dict:
    """Decodificar token de verificación de email."""
    payload = verify_token(token, "email_verification")
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired verification token"
        )
    
    return payload

def decode_password_reset_token(token: str) -> dict:
    """Decodificar token de reset de contraseña."""
    payload = verify_token(token, "password_reset")
    
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or expired reset token"
        )
    
    return payload

def get_password_hash(password: str) -> str:
    """Generar hash de contraseña."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verificar contraseña."""
    return pwd_context.verify(plain_password, hashed_password)