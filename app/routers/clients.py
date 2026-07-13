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
from app.schemas.whatsapp import ClientLoanRepaymentCreate, WhatsAppBalanceAdjust
from app.services.client_loan_service import ClientLoanService
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_quote_service import QuoteServiceError

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.get("/{client_uuid}/loans")
async def get_client_loans(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        return ClientLoanService(db).list_for_client(client_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/loans/{loan_uuid}/repayments", status_code=status.HTTP_201_CREATED)
async def add_client_loan_repayment(
    client_uuid: UUID,
    loan_uuid: UUID,
    payload: ClientLoanRepaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = ClientLoanService(db)
    try:
        loan = service._loan_by_uuid(loan_uuid)
        if str(loan.client.uuid) != str(client_uuid):
            raise QuoteServiceError("loan_client_mismatch", "El préstamo no pertenece al cliente", 404)
        return service.add_repayment(
            loan_uuid,
            payload.preferred_amount,
            payload.notes,
            created_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


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
    balances = WhatsAppBalanceService(db).balances_by_client_ids([c.id for c in items])
    return ClientList(
        items=[ClientResponse(**c.dict(), balance=balances.get(c.id, 0.0)) for c in items],
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
    balance = WhatsAppBalanceService(db).get_balance(client.id)
    return ClientResponse(**client.dict(), balance=balance)


@router.get("/{client_uuid}/balance")
async def get_client_balance(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Saldo a favor + movimientos del cliente. {balance, currency, entries}."""
    try:
        return WhatsAppBalanceService(db).summary_by_uuid(client_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/balance/adjust", status_code=status.HTTP_201_CREATED)
async def adjust_client_balance(
    client_uuid: UUID,
    payload: WhatsAppBalanceAdjust,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    """Ajuste manual del saldo (CREDIT/DEBIT) con nota. Devuelve el movimiento + balance_after."""
    try:
        return WhatsAppBalanceService(db).adjust(
            client_uuid, payload.entry_type, payload.amount, payload.notes,
            created_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


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
