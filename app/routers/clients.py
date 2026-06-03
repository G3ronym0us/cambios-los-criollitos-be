"""
Router "Clientes" de cara al operador (front), autenticado con JWT humano.

Expone los clientes del bot (`whatsapp_clients`) bajo `/clients` — sin el prefijo
`whatsapp`, porque para el operador son simplemente "clientes" del negocio. El
router `/whatsapp/*` (X-Bot-Token) sigue siendo de uso exclusivo del bot.
"""

from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.whatsapp_client_repository import WhatsAppClientRepository
from app.schemas.client import ClientList, ClientResponse, ClientUpdate

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.get("", response_model=ClientList)
async def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None, description="Filtra por nombre o teléfono"),
    is_blocked: Optional[bool] = Query(None),
    is_tracked: Optional[bool] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista clientes del bot. Cualquier operador autenticado puede leer."""
    repo = WhatsAppClientRepository(db)
    items, total = repo.list(
        skip=skip, limit=limit, search=search,
        is_blocked=is_blocked, is_tracked=is_tracked,
    )
    return ClientList(
        items=[ClientResponse(**c.dict()) for c in items],
        total=total, skip=skip, limit=limit,
    )


@router.get("/{client_uuid}", response_model=ClientResponse)
async def get_client(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = WhatsAppClientRepository(db).get_by_uuid(client_uuid)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    return ClientResponse(**client.dict())


@router.patch("/{client_uuid}", response_model=ClientResponse)
async def update_client(
    client_uuid: UUID,
    payload: ClientUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    repo = WhatsAppClientRepository(db)
    client = repo.get_by_uuid(client_uuid)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")

    data = payload.model_dump(exclude_unset=True)

    # Resolver el par preferido (uuid -> id) si vino en el payload
    if "preferred_pair_uuid" in data:
        pair_uuid = data.pop("preferred_pair_uuid")
        if pair_uuid is None:
            client.preferred_pair_id = None
        else:
            pair = CurrencyPairRepository(db).get_by_uuid(pair_uuid)
            if pair is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Par de monedas no encontrado")
            client.preferred_pair_id = pair.id

    for field, value in data.items():
        setattr(client, field, value)

    db.commit()
    db.refresh(client)
    return ClientResponse(**client.dict())
