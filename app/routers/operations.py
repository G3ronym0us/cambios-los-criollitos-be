"""
Router "Operaciones" de cara al operador (front), autenticado con JWT humano.

Expone las operaciones del bot (`whatsapp_operations`) bajo `/operations` — sin
el prefijo `whatsapp`. El operador puede consultar y corregir sus datos administrativos;
el ciclo de vida (cotizar/aprobar/completar/entregar) lo maneja el bot vía
`/whatsapp/*` (X-Bot-Token). Reusa WhatsAppQuoteService y los schemas del bot.

No confundir con `transactions` (registro contable con profit splits): una
operación COMPLETED genera una Transaction, pero son etapas distintas.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.dependencies import get_current_user, get_moderator_user
from app.database.connection import get_db
from app.models.user import User
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.schemas.whatsapp import (
    WhatsAppBalanceDebit,
    WhatsAppOperationList,
    WhatsAppOperationResponse,
    WhatsAppOperationScenarioUpdate,
    WhatsAppOperationStatusUpdate,
    WhatsAppOperationUpdate,
    WhatsAppPartialSettle,
    WhatsAppStatsResponse,
)
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
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

    # Marca qué operaciones ya tienen un pago entrante/saliente vinculado, para que el
    # selector de "vincular pago" pueda ocultar las que ya están tomadas de ese lado.
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


@router.get("/{op_uuid}/payments")
async def get_operation_payments(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pagos entrantes y salientes vinculados a la operación (para el detalle)."""
    service = WhatsAppPaymentService(db)
    try:
        return service.list_payments_for_operation(op_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{op_uuid}/debit-balance", status_code=status.HTTP_201_CREATED)
async def debit_balance_for_operation(
    op_uuid: UUID,
    payload: WhatsAppBalanceDebit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Debita saldo a favor del cliente por esta operación de abono (default: from_amount USD)."""
    try:
        return WhatsAppBalanceService(db).debit_for_operation(
            op_uuid, payload.amount, payload.notes, created_by_user_id=current_user.id
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/{op_uuid}/delivered", response_model=WhatsAppOperationResponse)
async def mark_operation_delivered(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Recibe los USD, completa la operación y asegura su transacción."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.mark_delivered(op_uuid, current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.post("/{op_uuid}/partial-settle")
async def partial_settle_operation(
    op_uuid: UUID,
    payload: WhatsAppPartialSettle,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Corrección retroactiva de una op COMPLETED que se completó por el total cuando
    el cliente solo cambió una parte: redimensiona la op al monto realmente cambiado,
    sincroniza la transacción contable y acredita el excedente como saldo a favor.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.partial_settle_completed(op_uuid, payload.settle_amount, current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/{op_uuid}", response_model=WhatsAppOperationResponse)
async def update_operation(
    op_uuid: UUID,
    payload: WhatsAppOperationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edita cliente, escenario, grupo y receptor como una sola operación atómica."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.update_operation(op_uuid, payload, current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.patch("/{op_uuid}/status", response_model=WhatsAppOperationResponse)
async def update_operation_status(
    op_uuid: UUID,
    payload: WhatsAppOperationStatusUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cambia manualmente el estado; COMPLETED crea la transacción contable."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.update_status(op_uuid, payload.status, current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return WhatsAppOperationResponse.model_validate(op.dict())


@router.delete("/{op_uuid}")
async def delete_operation(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """
    Borra una operación que quedó sin ningún comprobante, junto con su transacción contable
    y los movimientos que dejó en el fondo. Requiere moderador.

    Rechaza si todavía tiene pagos vinculados o si movió el saldo a favor del cliente.
    """
    service = WhatsAppQuoteService(db)
    op = service.get_by_uuid(op_uuid)
    if op is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operación no encontrada")
    try:
        return service.delete_operation(op)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/{op_uuid}/scenario", response_model=WhatsAppOperationResponse)
async def update_operation_scenario(
    op_uuid: UUID,
    payload: WhatsAppOperationScenarioUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Edición manual del escenario/grupo/receptor del entrante desde el dashboard."""
    service = WhatsAppQuoteService(db)
    try:
        op = service.set_scenario(op_uuid, payload, current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)
    return WhatsAppOperationResponse.model_validate(op.dict())
