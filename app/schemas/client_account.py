"""
Schemas de la libreta de cuentas de un cliente: CRUD del front y resolución del bot.
"""

from datetime import datetime
from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field

# Monedas fiat en las que un cliente puede recibir.
AccountCurrency = Literal["VES", "BRL", "COP"]


class ClientAccountResponse(BaseModel):
    uuid: UUID
    alias: Optional[str] = None
    payment_info: str
    currency: str
    is_default: bool
    is_confirmed: bool
    source: str
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ClientAccountCreate(BaseModel):
    alias: Optional[str] = Field(None, max_length=120)
    payment_info: str = Field(..., min_length=1)
    currency: AccountCurrency
    is_default: bool = False
    # El operador que la crea a mano la está confirmando por definición.
    is_confirmed: bool = True


class ClientAccountUpdate(BaseModel):
    alias: Optional[str] = Field(None, max_length=120)
    payment_info: Optional[str] = Field(None, min_length=1)
    currency: Optional[AccountCurrency] = None
    is_default: Optional[bool] = None
    is_confirmed: Optional[bool] = None


class ClientAccountList(BaseModel):
    items: List[ClientAccountResponse]


class AccountResolveResponse(BaseModel):
    """
    Resultado de resolver un nombre contra la libreta del cliente.

    MATCH = una sola cuenta coincide (viene en `account`).
    NONE = ninguna; el bot cotiza sin datos y el alias queda pendiente de aprender.
    AMBIGUOUS = varias; el bot NO inyecta nada y avisa al operador con `candidates`.
    """
    status: Literal["MATCH", "NONE", "AMBIGUOUS"]
    account: Optional[ClientAccountResponse] = None
    candidates: List[ClientAccountResponse] = []
