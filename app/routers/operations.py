"""
Router "Operaciones" de cara al operador (front), autenticado con JWT humano.

Expone las operaciones del bot (`whatsapp_operations`) bajo `/operations` — sin
el prefijo `whatsapp`. Es de solo lectura (lista/detalle/stats): el ciclo de vida
(cotizar/aprobar/completar/entregar) lo maneja el bot vía `/whatsapp/*`
(X-Bot-Token). Reusa WhatsAppQuoteService y los schemas del bot.

No confundir con `transactions` (registro contable con profit splits): una
operación COMPLETED genera una Transaction, pero son etapas distintas.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.models.user import User
from app.schemas.whatsapp import (
    WhatsAppOperationList,
    WhatsAppOperationResponse,
    WhatsAppStatsResponse,
)
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService

router = APIRouter(prefix="/operations", tags=["Operations"])


@router.get("", response_model=WhatsAppOperationList)
async def list_operations(
    status_filter: Optional[str] = Query(None, alias="status"),
    delivery_status: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    since: Optional[datetime] = Query(None),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista operaciones del bot. Cualquier operador autenticado puede leer."""
    service = WhatsAppQuoteService(db)
    try:
        ops = service.list_operations(
            phone=phone,
            status=status_filter,
            since=since,
            limit=limit,
            delivery_status=delivery_status,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    items = [WhatsAppOperationResponse.model_validate(op.dict()) for op in ops]
    return WhatsAppOperationList(operations=items, total=len(items))


@router.get("/stats", response_model=WhatsAppStatsResponse)
async def get_operations_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = WhatsAppQuoteService(db)
    return WhatsAppStatsResponse(**service.get_stats())


@router.get("/{op_uuid}", response_model=WhatsAppOperationResponse)
async def get_operation(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    service = WhatsAppQuoteService(db)
    op = service.get_by_uuid(op_uuid)
    if op is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operación no encontrada")
    return WhatsAppOperationResponse.model_validate(op.dict())
