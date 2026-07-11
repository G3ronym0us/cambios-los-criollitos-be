"""
Router para el bot de WhatsApp. Todos los endpoints requieren X-Bot-Token
(o JWT humano para inspección desde el frontend, si aplica más adelante).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.core.bot_auth import BotPrincipal, get_bot_principal
from app.database.connection import get_db
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.schemas.whatsapp import (
    BcvRateResponse,
    WhatsAppBalanceCredit,
    WhatsAppBalanceDebit,
    WhatsAppClientResponse,
    WhatsAppClientUpsert,
    WhatsAppCreateOpFromPayment,
    WhatsAppForwardToGroup,
    WhatsAppIrrelevant,
    WhatsAppOperationApprove,
    WhatsAppOperationCancel,
    WhatsAppOperationComplete,
    WhatsAppOperationCreate,
    WhatsAppOperationList,
    WhatsAppOperationNotes,
    WhatsAppOperationResponse,
    WhatsAppOperationScenarioUpdate,
    WhatsAppPartnerList,
    WhatsAppPartnerResponse,
    WhatsAppPaymentCreate,
    WhatsAppPendingDepositCreate,
    WhatsAppPaymentLink,
    WhatsAppPaymentUpdate,
    WhatsAppPersonalExpense,
    WhatsAppStatsResponse,
)
from app.services.bcv_service import fetch_bcv_rate, get_cached_bcv_rate
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.fund_pending_deposit_service import FundPendingDepositService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService


_TABLE = Path(..., pattern="^(incoming|outgoing)$")


router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


def _handle_service_error(exc: QuoteServiceError):
    raise HTTPException(status_code=exc.http_status, detail={"code": exc.code, "message": exc.message})


# ---------- Operations ----------

@router.post(
    "/operations",
    response_model=WhatsAppOperationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_operation(
    payload: WhatsAppOperationCreate,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Crea una cotización normal o BCV en el backend."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.create_quote(payload)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/approve", response_model=WhatsAppOperationResponse)
def approve_operation(
    op_uuid: UUID,
    payload: WhatsAppOperationApprove,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    try:
        op = service.approve_quote(op_uuid, payload)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/cancel", response_model=WhatsAppOperationResponse)
def cancel_operation(
    op_uuid: UUID,
    payload: WhatsAppOperationCancel,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    try:
        op = service.cancel_operation(op_uuid, payload)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/restore", response_model=WhatsAppOperationResponse)
def restore_operation(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Revierte una cancelación reciente (CANCELLED → QUOTED). Lo usa el bot al
    detectar que una 'corrección de monto' era en realidad una operación aparte."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.restore_quote(op_uuid)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/complete", response_model=WhatsAppOperationResponse)
def complete_operation(
    op_uuid: UUID,
    payload: WhatsAppOperationComplete,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    try:
        op = service.complete_operation(op_uuid, payload, principal.service_user)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/notes", response_model=WhatsAppOperationResponse)
def attach_notes(
    op_uuid: UUID,
    payload: WhatsAppOperationNotes,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Adjunta/actualiza notas (datos de pago) en una op activa; opcionalmente QUOTED→PENDING."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.attach_notes(op_uuid, payload.notes, payload.set_pending)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/delivered", response_model=WhatsAppOperationResponse)
def mark_delivered(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Marca como recibidos los USD efectivo (delivery_status PENDING→RECEIVED)."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.mark_delivered(op_uuid)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/operations/{op_uuid}/scenario", response_model=WhatsAppOperationResponse)
def set_operation_scenario(
    op_uuid: UUID,
    payload: WhatsAppOperationScenarioUpdate,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Clasifica/edita el escenario, grupo (por uuid o group_jid) y receptor del entrante."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.set_scenario(op_uuid, payload, principal.service_user)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.get("/partners", response_model=WhatsAppPartnerList)
def list_partners(
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Socios (FundGroupMember con whatsapp_phone) que reportan entrantes desde su número."""
    service = WhatsAppQuoteService(db)
    partners = [WhatsAppPartnerResponse.model_validate(p) for p in service.list_partners()]
    return WhatsAppPartnerList(partners=partners, total=len(partners))


@router.get("/operations/stats", response_model=WhatsAppStatsResponse)
def get_stats(
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Conteos por estado + completados hoy, para el dashboard."""
    service = WhatsAppQuoteService(db)
    return WhatsAppStatsResponse(**service.get_stats())


@router.get("/operations/active", response_model=Optional[WhatsAppOperationResponse])
def get_active_operation(
    phone: str = Query(..., min_length=4, max_length=32),
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    op = service.get_active_for_phone(phone)
    if op is None:
        return None
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.get("/operations/{op_uuid}", response_model=WhatsAppOperationResponse)
def get_operation(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    op = service.get_by_uuid(op_uuid)
    if op is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Operation no encontrada"})
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.get("/operations", response_model=WhatsAppOperationList)
def list_operations(
    phone: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    since: Optional[datetime] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    delivery_status: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    try:
        ops = service.list_operations(
            phone=phone, status=status_filter, since=since, limit=limit, delivery_status=delivery_status
        )
    except QuoteServiceError as exc:
        _handle_service_error(exc)

    # Flags de pagos ya vinculados (mismo criterio que el endpoint del operador):
    # el matcher del bot los usa para NO volver a matchear una op que ya tiene su
    # saliente (evita que dos comprobantes del mismo monto caigan en la misma op).
    op_ids = [op.id for op in ops]
    inc_taken: set[int] = set()
    out_taken: set[int] = set()
    if op_ids:
        inc_taken = {
            r[0] for r in db.query(WhatsAppIncomingPayment.whatsapp_operation_id)
            .filter(WhatsAppIncomingPayment.whatsapp_operation_id.in_(op_ids)).distinct().all()
        }
        out_taken = {
            r[0] for r in db.query(WhatsAppOutgoingPayment.whatsapp_operation_id)
            .filter(WhatsAppOutgoingPayment.whatsapp_operation_id.in_(op_ids)).distinct().all()
        }

    items = []
    for op in ops:
        d = op.dict()
        d["has_incoming_payment"] = op.id in inc_taken
        d["has_outgoing_payment"] = op.id in out_taken
        items.append(WhatsAppOperationResponse.model_validate(d))
    return WhatsAppOperationList(operations=items, total=len(items))


# ---------- Clients ----------

@router.get("/clients/{phone}", response_model=WhatsAppClientResponse)
def get_client(
    phone: str,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    client = db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
    if client is None:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "Cliente no encontrado"})
    return WhatsAppClientResponse.model_validate(client.dict())


@router.put("/clients/{phone}", response_model=WhatsAppClientResponse)
def upsert_client(
    phone: str,
    payload: WhatsAppClientUpsert,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    client = service.upsert_client(phone, payload.display_name)

    if payload.preferred_pair_uuid is not None:
        pair_repo = CurrencyPairRepository(db)
        pair = pair_repo.get_by_uuid(payload.preferred_pair_uuid)
        if pair is None:
            raise HTTPException(status_code=404, detail={"code": "pair_not_found", "message": "Currency pair no existe"})
        client.preferred_pair_id = pair.id

    if payload.is_tracked is not None:
        client.is_tracked = payload.is_tracked
    if payload.is_blocked is not None:
        client.is_blocked = payload.is_blocked
    if payload.is_usdt_authorized is not None:
        client.is_usdt_authorized = payload.is_usdt_authorized

    db.commit()
    db.refresh(client)
    return WhatsAppClientResponse.model_validate(client.dict())


# ---------- Saldo a favor (ledger) ----------

@router.get("/clients/{phone}/balance")
def get_client_balance(
    phone: str,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Saldo a favor + movimientos del cliente (por teléfono). {balance, currency, entries}."""
    try:
        return WhatsAppBalanceService(db).summary_by_phone(phone)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.post("/payments/incoming/{payment_id}/credit-balance", status_code=status.HTTP_201_CREATED)
def credit_balance_from_incoming(
    payment_id: int,
    payload: WhatsAppBalanceCredit,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Acredita un pago entrante (Zelle/PayPal/USD) como saldo a favor del cliente."""
    try:
        return WhatsAppBalanceService(db).credit_from_incoming(
            payment_id, payload.amount, payload.notes,
            created_by_user_id=principal.service_user.id,
        )
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.post("/operations/{op_uuid}/debit-balance", status_code=status.HTTP_201_CREATED)
def debit_balance_for_operation(
    op_uuid: UUID,
    payload: WhatsAppBalanceDebit,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Debita saldo del cliente por una operación de abono (default: from_amount USD)."""
    try:
        return WhatsAppBalanceService(db).debit_for_operation(
            op_uuid, payload.amount, payload.notes,
            created_by_user_id=principal.service_user.id,
        )
    except QuoteServiceError as exc:
        _handle_service_error(exc)


# ---------- Payments (comprobantes OCR) ----------

@router.get("/payments/corrected")
def list_corrected_payments(
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    return WhatsAppPaymentService(db).list_corrected()


@router.post("/payments/{table}", status_code=status.HTTP_201_CREATED)
def create_payment(
    payload: WhatsAppPaymentCreate,
    table: str = _TABLE,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.create_payment(table, payload)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.get("/payments/{table}")
def list_payments(
    table: str = _TABLE,
    limit: int = Query(200, ge=1, le=1000),
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.list_payments(table, limit)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.patch("/payments/{table}/{payment_id}")
def update_payment(
    payment_id: int,
    payload: WhatsAppPaymentUpdate,
    table: str = _TABLE,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.update_payment(table, payment_id, payload.dict(exclude_unset=True))
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.put("/payments/{table}/{payment_id}/operation")
def link_payment_operation(
    payment_id: int,
    payload: WhatsAppPaymentLink,
    table: str = _TABLE,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation(table, payment_id, payload.operation_uuid)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.post("/payments/{table}/{payment_id}/create-operation")
def create_operation_from_payment(
    payment_id: int,
    payload: WhatsAppCreateOpFromPayment,
    table: str = _TABLE,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.create_operation_from_payment(
            table, payment_id, payload.from_currency, payload.to_currency,
            payload.from_amount, payload.to_amount,
            recorded_by_user_id=principal.service_user.id,
        )
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.patch("/payments/incoming/{payment_id}/forward-to-group")
def forward_incoming_to_group(
    payment_id: int,
    payload: WhatsAppForwardToGroup,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Marca un pago entrante como contabilizado en un grupo (ZELLE_DIRECT). No crea saliente."""
    service = WhatsAppPaymentService(db)
    try:
        return service.mark_incoming_forwarded_to_group(payment_id, payload.group_jid, payload.group_uuid)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.patch("/payments/outgoing/{payment_id}/personal-expense")
def set_personal_expense(
    payment_id: int,
    payload: WhatsAppPersonalExpense,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.set_personal_expense(payment_id, payload.is_personal_expense, payload.personal_description)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


@router.patch("/payments/outgoing/{payment_id}/irrelevant")
def set_irrelevant(
    payment_id: int,
    payload: WhatsAppIrrelevant,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppPaymentService(db)
    try:
        return service.set_irrelevant(payment_id, payload.is_irrelevant, payload.irrelevant_description)
    except QuoteServiceError as exc:
        _handle_service_error(exc)


# ---------- Depósitos pendientes (detectados por el bot en el grupo) ----------

@router.post("/pending-deposits", status_code=status.HTTP_201_CREATED)
def create_pending_deposit(
    payload: WhatsAppPendingDepositCreate,
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Un gestor subió un comprobante al grupo → crea un depósito PENDING (confirmable en /admin/funds)."""
    service = FundPendingDepositService(db)
    try:
        return service.create_pending(
            group_jid=payload.group_jid,
            detected_phone=payload.detected_phone,
            amount=payload.amount,
            currency=payload.currency,
            provider=payload.provider,
            reference=payload.reference,
            raw_text=payload.raw_text,
        )
    except QuoteServiceError as exc:
        _handle_service_error(exc)


# ---------- BCV ----------

@router.get("/bcv", response_model=Optional[BcvRateResponse])
def get_bcv(
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    from app.models.bcv_rate import BcvRate
    latest = db.query(BcvRate).order_by(BcvRate.fetched_at.desc()).first()
    if latest is None:
        return None
    return BcvRateResponse.model_validate(latest.dict())


@router.post("/bcv/refresh", response_model=Optional[BcvRateResponse])
async def refresh_bcv(
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    """Forzar refresh sincrónico de la tasa BCV (útil para ops sin Celery worker)."""
    rate = await fetch_bcv_rate(db)
    if rate is None:
        return None
    from app.models.bcv_rate import BcvRate
    latest = db.query(BcvRate).order_by(BcvRate.fetched_at.desc()).first()
    return BcvRateResponse.model_validate(latest.dict()) if latest else None
