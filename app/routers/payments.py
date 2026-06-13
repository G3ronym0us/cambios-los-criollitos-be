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
from app.schemas.whatsapp import WhatsAppIrrelevant, WhatsAppPaymentLink, WhatsAppPersonalExpense
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/{table}")
async def list_payments(
    table: Literal["incoming", "outgoing"],
    limit: int = Query(300, ge=1, le=1000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Lista pagos entrantes o salientes del bot. Cualquier operador autenticado."""
    service = WhatsAppPaymentService(db)
    try:
        return service.list_payments(table, limit)
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
    """Vincula (o desvincula con operation_uuid=null) un pago a una operación. Operador JWT."""
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation(table, payment_id, payload.operation_uuid)
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
