"""
Libreta de cuentas de un cliente, para el panel del operador.

Vive aparte de `clients.py` porque es su propio recurso: el CRUD de cuentas no tiene nada
que ver con editar el perfil del cliente.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.repositories.whatsapp_client_account_repository import WhatsAppClientAccountRepository
from app.repositories.whatsapp_client_repository import WhatsAppClientRepository
from app.schemas.client_account import (
    ClientAccountCreate,
    ClientAccountList,
    ClientAccountResponse,
    ClientAccountUpdate,
)
from app.services.beneficiary_accounts import normalize_alias

router = APIRouter(prefix="/clients", tags=["client-accounts"])


def _get_account_or_404(db: Session, account_uuid: UUID):
    account = WhatsAppClientAccountRepository(db).get_by_uuid(account_uuid)
    if account is None:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada")
    return account


@router.get("/{client_uuid}/accounts", response_model=ClientAccountList)
def list_client_accounts(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    client = WhatsAppClientRepository(db).get_by_uuid(client_uuid)
    if client is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    items = WhatsAppClientAccountRepository(db).list_for_client(client.id)
    return ClientAccountList(items=[ClientAccountResponse.model_validate(i) for i in items])


@router.post(
    "/{client_uuid}/accounts",
    response_model=ClientAccountResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_client_account(
    client_uuid: UUID,
    payload: ClientAccountCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    client = WhatsAppClientRepository(db).get_by_uuid(client_uuid)
    if client is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    repo = WhatsAppClientAccountRepository(db)
    if repo.get_by_payment_info(client.id, payload.payment_info) is not None:
        raise HTTPException(status_code=409, detail="El cliente ya tiene esa cuenta guardada")
    account = repo.create(
        client_id=client.id,
        alias=payload.alias,
        payment_info=payload.payment_info,
        currency=payload.currency,
        source="MANUAL",
        is_confirmed=payload.is_confirmed,
        is_default=payload.is_default,
    )
    return ClientAccountResponse.model_validate(account)


@router.patch("/accounts/{account_uuid}", response_model=ClientAccountResponse)
def update_client_account(
    account_uuid: UUID,
    payload: ClientAccountUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    account = _get_account_or_404(db, account_uuid)
    repo = WhatsAppClientAccountRepository(db)
    fields = payload.model_dump(exclude_unset=True)

    if "alias" in fields:
        account.alias = (fields["alias"] or "").strip() or None
        account.alias_normalized = normalize_alias(account.alias)
    if "payment_info" in fields and fields["payment_info"]:
        account.payment_info = fields["payment_info"]
    if "currency" in fields and fields["currency"]:
        account.currency = fields["currency"].upper()
    if "is_confirmed" in fields:
        account.is_confirmed = bool(fields["is_confirmed"])
    if fields.get("is_default"):
        repo.set_default(account.client_id, account)
    elif "is_default" in fields:
        account.is_default = False

    db.commit()
    db.refresh(account)
    return ClientAccountResponse.model_validate(account)


@router.delete("/accounts/{account_uuid}", status_code=status.HTTP_204_NO_CONTENT)
def delete_client_account(
    account_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    account = _get_account_or_404(db, account_uuid)
    WhatsAppClientAccountRepository(db).delete(account)
