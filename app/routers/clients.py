"""
Router "Clientes" de cara al operador (front), autenticado con JWT humano.

Expone los clientes del bot (`whatsapp_clients`) bajo `/clients` — sin el prefijo
`whatsapp`, porque para el operador son simplemente "clientes" del negocio. El
router `/whatsapp/*` (X-Bot-Token) sigue siendo de uso exclusivo del bot.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.whatsapp_client_repository import WhatsAppClientRepository
from app.schemas.client import (
    ClientCreate,
    ClientList,
    ClientResponse,
    ClientUpdate,
    PendingDeliveryCreate,
)
from app.schemas.whatsapp import ClientLoanManualCreate, ClientLoanRepaymentCreate, WhatsAppBalanceAdjust
from app.services.client_entity_service import ClientEntityService
from app.services.client_pending_service import ClientPendingService
from app.services.client_loan_service import ClientLoanService
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_client_account_service import WhatsAppClientAccountService
from app.services.whatsapp_quote_service import QuoteServiceError

router = APIRouter(prefix="/clients", tags=["Clients"])


@router.get("/{client_uuid}/loans")
async def get_client_loans(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    try:
        return ClientLoanService(db).list_for_client(client_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{client_uuid}/loans/valuation")
async def preview_manual_loan_valuation(
    client_uuid: UUID,
    amount: float = Query(..., gt=0),
    currency: str = Query(..., min_length=2, max_length=10),
    at: datetime = Query(..., description="Fecha del préstamo (ISO 8601)"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Equivalencias de un préstamo sin comprobante, con las tasas de la fecha indicada."""
    try:
        return ClientLoanService(db).preview_manual(client_uuid, amount, currency, at)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/loans", status_code=status.HTTP_201_CREATED)
async def create_manual_loan(
    client_uuid: UUID,
    payload: ClientLoanManualCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Registra un préstamo a mano, sin comprobante."""
    try:
        return ClientLoanService(db).create_manual(
            client_uuid=client_uuid,
            preferred_value=payload.preferred_value,
            fiat_currency=payload.fiat_currency,
            fiat_amount=payload.fiat_amount,
            valuation_at=payload.valuation_at,
            usdt_amount=payload.usdt_amount,
            bcv_amount=payload.bcv_amount,
            notes=payload.notes,
            created_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/loans/{loan_uuid}/repayments", status_code=status.HTTP_201_CREATED)
async def add_client_loan_repayment(
    client_uuid: UUID,
    loan_uuid: UUID,
    payload: ClientLoanRepaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
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


@router.post("", response_model=ClientResponse, status_code=status.HTTP_201_CREATED)
async def create_entity_client(
    payload: ClientCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    """Da de alta un negocio sin teléfono propio como cliente-entidad."""
    try:
        entity = ClientEntityService(db).create(payload.display_name, payload.linked_group_jid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return ClientResponse(**entity.dict(), balance=0.0)


@router.get("", response_model=ClientList)
async def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    search: Optional[str] = Query(None, description="Filtra por nombre o teléfono"),
    is_blocked: Optional[bool] = Query(None),
    is_tracked: Optional[bool] = Query(None),
    has_pending: Optional[bool] = Query(
        None, description="Sólo los clientes a los que les debemos algo (o sólo los que no)"
    ),
    pair: Optional[str] = Query(
        None, description="Acota `has_pending` y `pending_by_pair` a un par ('USD/VES')"
    ),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """
    Lista clientes del bot. Cualquier operador autenticado puede leer.

    `pair` recorta lo que se ve del cliente, no sólo qué clientes salen: con `USD/VES`, uno
    que además deba en VES/COP devuelve sólo la entrada de USD/VES.
    """
    repo = WhatsAppClientRepository(db)
    pending_service = ClientPendingService(db)

    pending_ids = (
        pending_service.client_ids_with_pending(pair) if has_pending is not None else None
    )
    items, total = repo.list(
        skip=skip, limit=limit, search=search,
        is_blocked=is_blocked, is_tracked=is_tracked,
        has_pending=has_pending, pending_client_ids=pending_ids,
    )

    client_ids = [c.id for c in items]
    balances = WhatsAppBalanceService(db).balances_by_client_ids(client_ids)
    # Una sola consulta agregada para toda la página, no una por cliente.
    pending = pending_service.pending_by_client_ids(client_ids, pair)
    return ClientList(
        items=[
            ClientResponse(
                **c.dict(),
                balance=balances.get(c.id, 0.0),
                pending_by_pair=pending.get(c.id, []),
            )
            for c in items
        ],
        total=total, skip=skip, limit=limit,
    )


@router.get("/{client_uuid}", response_model=ClientResponse)
async def get_client(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    client = WhatsAppClientRepository(db).get_by_uuid(client_uuid)
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente no encontrado")
    balance = WhatsAppBalanceService(db).get_balance(client.id)
    pending = ClientPendingService(db).pending_by_client_ids([client.id])
    return ClientResponse(
        **client.dict(), balance=balance, pending_by_pair=pending.get(client.id, [])
    )


@router.get("/{client_uuid}/balance")
async def get_client_balance(
    client_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
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

    # `default_payment_info`/`default_payment_currency` ya no son columnas: se resuelven
    # contra la cuenta predeterminada de la libreta (whatsapp_client_accounts).
    default_info = data.pop("default_payment_info", None)
    default_currency = data.pop("default_payment_currency", None)

    for field, value in data.items():
        setattr(client, field, value)

    if "default_payment_info" in payload.model_fields_set:
        WhatsAppClientAccountService(db).set_default_account(client, default_info, default_currency)

    db.commit()
    db.refresh(client)
    return ClientResponse(**client.dict())


@router.get("/{client_uuid}/pending/deliveries")
async def list_pending_deliveries(
    client_uuid: UUID,
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """Las entregas marcadas del cliente, con lo que había antes de cada una."""
    try:
        return ClientPendingService(db).history(client_uuid, limit=limit)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/pending/deliver", status_code=status.HTTP_201_CREATED)
async def deliver_pending(
    client_uuid: UUID,
    payload: PendingDeliveryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    """
    Marca como entregado en efectivo un lote de operaciones sin cubrir.

    O todas o ninguna: una entrega a medias deja dinero marcado sin que nadie sepa cuánto.
    Devuelve el lote con su uuid, que es lo que hace falta para deshacerlo.
    """
    try:
        return ClientPendingService(db).deliver(
            client_uuid,
            [item.model_dump() for item in payload.operations],
            payload.note,
            actor=current_user,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{client_uuid}/pending/deliveries/{delivery_uuid}/undo")
async def undo_pending_delivery(
    client_uuid: UUID,
    delivery_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),  # mutación: moderador+
):
    """
    Devuelve las operaciones del lote a como estaban, sin borrar el rastro.

    Sin límite de tiempo: si el error se descubre mañana, se deshace mañana.
    """
    try:
        return ClientPendingService(db).undo(client_uuid, delivery_uuid, actor=current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
