"""
Router para el bot de WhatsApp. Todos los endpoints requieren X-Bot-Token
(o JWT humano para inspección desde el frontend, si aplica más adelante).
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.bot_auth import BotPrincipal, get_bot_principal
from app.database.connection import get_db
from app.models.whatsapp_client import WhatsAppClient
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.schemas.whatsapp import (
    BcvRateResponse,
    WhatsAppClientResponse,
    WhatsAppClientUpsert,
    WhatsAppOperationApprove,
    WhatsAppOperationCancel,
    WhatsAppOperationComplete,
    WhatsAppOperationCreate,
    WhatsAppOperationList,
    WhatsAppOperationResponse,
)
from app.services.bcv_service import fetch_bcv_rate, get_cached_bcv_rate
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService


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
    """Crea una cotización (status=QUOTED). Cancela cotizaciones previas QUOTED del mismo cliente."""
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
    db: Session = Depends(get_db),
    principal: BotPrincipal = Depends(get_bot_principal),
):
    service = WhatsAppQuoteService(db)
    try:
        ops = service.list_operations(phone=phone, status=status_filter, since=since, limit=limit)
    except QuoteServiceError as exc:
        _handle_service_error(exc)
    items = [WhatsAppOperationResponse.model_validate(op.dict()) for op in ops]
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
