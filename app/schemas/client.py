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
    # Cuenta de pago predeterminada del cliente (bloque de datos + moneda fiat).
    default_payment_info: Optional[str] = None
    default_payment_currency: Optional[str] = None
    # Grupo de WhatsApp vinculado; solo lo llevan los clientes-entidad.
    linked_group_jid: Optional[str] = None
    # Saldo a favor en USD (ledger whatsapp_balance_entries); 0 si no tiene.
    balance: float = 0.0
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
    # Cuenta de pago predeterminada; enviar null para borrarla.
    default_payment_info: Optional[str] = None
    default_payment_currency: Optional[str] = None
    # Grupo vinculado de una entidad; enviar null para desvincular.
    linked_group_jid: Optional[str] = None


class ClientList(BaseModel):
    items: List[ClientResponse]
    total: int
    skip: int
    limit: int


class ClientCreate(BaseModel):
    """
    Alta manual de un cliente-entidad (un negocio sin teléfono en el bot). Los clientes
    normales no se crean por aquí: nacen del tráfico del bot.
    """
    display_name: str
    linked_group_jid: Optional[str] = None
