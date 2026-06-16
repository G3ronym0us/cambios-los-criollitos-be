"""
Autenticación para scripts externos que empujan tasas al backend.

El script del operador envía `X-API-Key: <EXTERNAL_RATE_API_KEY>` en cada request.
La dependencia `verify_external_rate_key` valida el token contra la variable de
entorno. Es auth de máquina (no JWT), pensada para procesos automáticos.
"""

from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_external_rate_key(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> bool:
    """Falla con 401 si la API key no es válida, 500 si no está configurada."""
    configured = getattr(settings, "EXTERNAL_RATE_API_KEY", None)
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="EXTERNAL_RATE_API_KEY no configurado en el backend",
        )
    if not x_api_key or x_api_key != configured:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="X-API-Key inválida o ausente",
        )
    return True
