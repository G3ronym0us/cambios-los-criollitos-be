"""
Autenticación del bot de WhatsApp como service account.

El bot envía `X-Bot-Token: <BOT_API_KEY>` en cada request. La dependencia
`get_bot_principal` valida el token y devuelve un `BotPrincipal` con
el `service_user_id` que se usa para crear Transactions (campo NOT NULL).

`get_bot_or_user` acepta cualquiera de los dos (bot o JWT humano).
"""

from dataclasses import dataclass
from typing import Optional, Union

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.dependencies import get_optional_user
from app.database.connection import get_db
from app.models.user import User


@dataclass
class BotPrincipal:
    """Identidad del bot autenticado (no es un User)."""

    service_user: User  # User que el bot "encarna" para satisfacer FK NOT NULL


def _resolve_service_user(db: Session) -> User:
    email = getattr(settings, "BOT_SERVICE_USER_EMAIL", None)
    if not email:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOT_SERVICE_USER_EMAIL no configurado",
        )
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"BOT_SERVICE_USER_EMAIL apunta a un usuario inexistente: {email}",
        )
    return user


def get_bot_principal(
    request: Request,
    x_bot_token: Optional[str] = Header(default=None, alias="X-Bot-Token"),
    db: Session = Depends(get_db),
) -> BotPrincipal:
    """Falla con 401 si el token no es válido."""
    configured_token = getattr(settings, "BOT_API_KEY", None)
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="BOT_API_KEY no configurado en el backend",
        )
    if not x_bot_token or x_bot_token != configured_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-Bot-Token inválido o ausente",
        )
    return BotPrincipal(service_user=_resolve_service_user(db))


async def get_bot_or_user(
    request: Request,
    x_bot_token: Optional[str] = Header(default=None, alias="X-Bot-Token"),
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
) -> Union[BotPrincipal, User]:
    """
    Acepta autenticación del bot (X-Bot-Token) o JWT humano (Authorization/Cookie).
    """
    configured_token = getattr(settings, "BOT_API_KEY", None)
    if x_bot_token and configured_token and x_bot_token == configured_token:
        return BotPrincipal(service_user=_resolve_service_user(db))

    if user is not None:
        return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Se requiere X-Bot-Token o token JWT válido",
    )
