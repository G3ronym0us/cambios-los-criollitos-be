"""
Router "Pagos" de cara al operador (front), autenticado con JWT humano.

Expone los pagos del bot (`whatsapp_incoming_payments` / `whatsapp_outgoing_payments`)
bajo `/payments/{table}` — sin el prefijo `whatsapp`. Solo lectura: la captura
de comprobantes (OCR) y el matching los hace el bot vía `/whatsapp/payments/*`
(X-Bot-Token). Reusa WhatsAppPaymentService.

- incoming = pagos que el cliente reporta haber enviado.
- outgoing = pagos que el operador emite (con flags personal/irrelevante y
  posible cadena source_payment_id desde un incoming).
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.models.user import User
from app.schemas.whatsapp import (
    WhatsAppBalanceCredit,
    WhatsAppCreateOpManual,
    WhatsAppForwardToGroup,
    WhatsAppIrrelevant,
    WhatsAppPaymentDeposit,
    WhatsAppPaymentLink,
    WhatsAppPersonalExpense,
    ClientLoanCreate,
)
from app.services.client_loan_service import ClientLoanService
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.post("/outgoing/{payment_id}/loan", status_code=201)
async def create_client_loan(
    payment_id: int,
    payload: ClientLoanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Registra un pago saliente como préstamo al cliente."""
    try:
        return ClientLoanService(db).create_from_outgoing(
            payment_id=payment_id,
            preferred_value=payload.preferred_value,
            preferred_amount=payload.preferred_amount,
            fiat_currency=payload.fiat_currency,
            notes=payload.notes,
            created_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{table}")
async def list_payments(
    table: Literal["incoming", "outgoing"],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    out_class: str = Query("ALL"),
    unlinked_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Página de pagos (paginada + búsqueda/clasificación server-side). Devuelve {items, total}."""
    service = WhatsAppPaymentService(db)
    try:
        return service.list_payments_page(
            table,
            limit,
            offset,
            search,
            out_class,
            unlinked_only,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/{table}/{payment_id}/operation")
async def link_payment_operation(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    payload: WhatsAppPaymentLink,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Vincula un pago; si es saliente, completa la operación activa y su transacción.
    Con `settle_amount` (solo salientes) la op se redimensiona al monto realmente
    cambiado y el excedente se acredita como saldo a favor del cliente.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation(
            table,
            payment_id,
            payload.operation_uuid,
            completing_user=current_user,
            complete_outgoing=True,
            settle_amount=payload.settle_amount,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/outgoing/{payment_id}/personal-expense")
async def mark_personal_expense(
    payment_id: int,
    payload: WhatsAppPersonalExpense,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Marca/desmarca un pago saliente como gasto personal (auto-desvincula la op). Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.set_personal_expense(
            payment_id, payload.is_personal_expense, payload.personal_description
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/outgoing/{payment_id}/irrelevant")
async def mark_irrelevant(
    payment_id: int,
    payload: WhatsAppIrrelevant,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Marca/desmarca un pago saliente como irrelevante (auto-desvincula la op). Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.set_irrelevant(
            payment_id, payload.is_irrelevant, payload.irrelevant_description
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/outgoing/{payment_id}/to-group-incoming")
async def convert_outgoing_to_group_incoming(
    payment_id: int,
    payload: WhatsAppForwardToGroup,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Convierte un saliente (Zelle reenviado al grupo) en un entrante contabilizado en el grupo. Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.convert_outgoing_to_group_incoming(payment_id, payload.group_jid, payload.group_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{table}/{payment_id}/create-operation")
async def create_operation_from_payment(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    payload: WhatsAppCreateOpManual,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Crea una operación a mano desde un pago y lo vincula. Soporta fondo (+EXCHANGE). Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.create_operation_from_payment(
            table,
            payment_id,
            payload.from_currency,
            payload.to_currency,
            payload.from_amount,
            payload.to_amount,
            amount_side=payload.amount_side,
            fund_group_uuid=payload.fund_group_uuid,
            exchange_user_uuid=payload.exchange_user_uuid,
            recorded_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/incoming/{payment_id}/credit-balance", status_code=201)
async def credit_balance_from_incoming(
    payment_id: int,
    payload: WhatsAppBalanceCredit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Acredita un pago entrante (Zelle/PayPal/USD) como saldo a favor del cliente. Operador JWT."""
    try:
        return WhatsAppBalanceService(db).credit_from_incoming(
            payment_id, payload.amount, payload.notes, created_by_user_id=current_user.id
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/incoming/{payment_id}/deposit")
async def register_payment_deposit(
    payment_id: int,
    payload: WhatsAppPaymentDeposit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Registra un pago entrante como depósito (FundMovement DEPOSIT) a un fondo. Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.create_deposit_from_payment(
            payment_id,
            payload.group_uuid,
            payload.user_uuid,
            payload.amount,
            payload.currency,
            payload.deposit_method,
            payload.reference,
            payload.notes,
            recorded_by_user_id=current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
