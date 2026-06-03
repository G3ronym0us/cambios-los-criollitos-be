"""
Schemas de "Clientes" de cara al operador (front).

Modelan `WhatsAppClient` (clientes del bot, identificados por teléfono), pero
con naming de negocio "cliente" — sin exponer el detalle de que vienen del bot.
No confundir con `users` (operadores/socios del sistema, con login y rol).
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel


class ClientResponse(BaseModel):
    uuid: UUID
    phone: str
    display_name: Optional[str] = None
    preferred_pair_uuid: Optional[UUID] = None
    preferred_pair_symbol: Optional[str] = None
    is_tracked: bool
    is_blocked: bool
    is_usdt_authorized: bool
    last_seen_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ClientUpdate(BaseModel):
    display_name: Optional[str] = None
    is_tracked: Optional[bool] = None
    is_blocked: Optional[bool] = None
    is_usdt_authorized: Optional[bool] = None
    # Par preferido por uuid; enviar null para desasignar.
    preferred_pair_uuid: Optional[UUID] = None


class ClientList(BaseModel):
    items: List[ClientResponse]
    total: int
    skip: int
    limit: int
