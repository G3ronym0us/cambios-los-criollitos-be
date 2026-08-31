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
    OperationCoverageUpdate,
    ProfitAllocationList,
    ProfitAllocationResponse,
    ProfitAllocationUpdate,
    WhatsAppBalanceDebit,
    WhatsAppOperationList,
    WhatsAppOperationResponse,
    WhatsAppOperationScenarioUpdate,
    WhatsAppOperationStatusUpdate,
    WhatsAppOperationUpdate,
    WhatsAppOperationValue,
    WhatsAppStatsResponse,
)
from app.schemas.operation_match import (
    OperationRankRequest,
    OperationRankResponse,
    OperationScoreResponse,
    SuggestionResponse,
)
from app.services.operation_match_service import OperationMatchService
from app.services.profit_allocation_service import ProfitAllocationService
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService

router = APIRouter(prefix="/operations", tags=["Operations"])


@router.get("", response_model=WhatsAppOperationList)
async def list_operations(
    status_filter: Optional[str] = Query(None, alias="status"),
    delivery_status: Optional[str] = Query(None),
    scenario: Optional[str] = Query(None),
    needs: Optional[str] = Query(
        None,
        description="Filtra por lo que hace falta hacer: settle | deliver | client | expiring | action",
    ),
    phone: Optional[str] = Query(None),
    search: Optional[str] = Query(None, description="Nombre o teléfono del cliente"),
    since: Optional[datetime] = Query(None),
    order_by: str = Query(
        "paid",
        description="Fecha por la que ordenar: paid (comprobante de salida) | created",
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lista operaciones del bot, paginada. Cualquier operador autenticado puede leer.

    `total` es el total tras los filtros, no el tamaño de la página: el listado del admin
    lo necesita para dibujar el pie («26–50 de 312») y saber si hay página siguiente.
    """
    service = WhatsAppQuoteService(db)
    try:
        ops, total = service.list_operations(
            phone=phone,
            status=status_filter,
            since=since,
            limit=limit,
            delivery_status=delivery_status,
            offset=(page - 1) * limit,
            search=search,
            scenario=scenario,
            needs=needs,
            order_by=order_by,
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
    return WhatsAppOperationList(operations=items, total=total, page=page, limit=limit)


@router.post("/match", response_model=OperationRankResponse)
async def rank_operations_for_payment(
    payload: OperationRankRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Puntúa las operaciones recientes contra un comprobante, para que el selector de "vincular
    pago" las ordene y marque la más probable. Misma implementación que usa el matcher
    automático del bot (`app/services/operation_match_service.py`), con la política del
    operador: aquí se sugiere y él confirma, así que es más permisiva que la del bot.
    """
    service = OperationMatchService(db)
    scored, suggestion = service.rank_for_payment(
        payload.payment_id, payload.table, limit=payload.limit
    )
    return OperationRankResponse(
        suggestion=(
            SuggestionResponse(uuid=suggestion.uuid, confident=suggestion.confident)
            if suggestion
            else None
        ),
        candidates=[OperationScoreResponse(**vars(s)) for s in scored],
    )


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


@router.patch("/{op_uuid}/value", response_model=WhatsAppOperationResponse)
async def update_operation_value(
    op_uuid: UUID,
    payload: WhatsAppOperationValue,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Corrige cuánto vale el trato, hacia arriba o hacia abajo. Reescala la cotización, recorta
    el reparto de los entrantes si el valor baja y recalcula el estado con lo entregado.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation_value(op_uuid, payload.amount, actor=current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{op_uuid}/coverage")
async def get_operation_coverage(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Qué cubre ya la operación y con qué comprobantes del cliente podría terminar de cubrirse.

    Espejo de `/payments/outgoing/{id}/settlements`: la misma tabla leída por la otra columna.
    Anclar en la operación es lo que permite cuadrar un trato pagado en partes sin llevar la
    suma de cabeza.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.operation_coverage(op_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.put("/{op_uuid}/coverage")
async def set_operation_coverage(
    op_uuid: UUID,
    payload: OperationCoverageUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Fija con qué comprobantes se cubre la operación. Al cuadrarla la tasa se deriva de la suma;
    el monto de la pata que sale no se teclea nunca.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation_coverage(
            op_uuid,
            payments=[p.dict() for p in payload.payments],
            value_amount=payload.value_amount,
            uncovered=payload.uncovered.dict() if payload.uncovered else None,
            partial=payload.partial,
            actor=current_user,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


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


@router.get("/{op_uuid}/profit-allocations", response_model=ProfitAllocationList)
async def get_profit_allocations(
    op_uuid: UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Quién se queda con el margen de esta operación, y cuánto quedó sin asignar."""
    service = WhatsAppQuoteService(db)
    op = service.get_by_uuid(op_uuid)
    if op is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operación no encontrada")
    return _profit_allocation_list(db, op)


@router.put("/{op_uuid}/profit-allocations", response_model=ProfitAllocationList)
async def set_profit_allocations(
    op_uuid: UUID,
    payload: ProfitAllocationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_moderator_user),
):
    """
    Redefine el reparto: normalmente todo al fondo por su porcentaje, pero admite varios
    destinos (otro fondo, o devolverle parte al cliente que hizo de intermediario).

    Repartir más de lo cobrado se acepta y queda firmado con quién lo aprobó. Al guardar se
    recalcula la ganancia de la transacción y el reparto entre socios. Requiere moderador.
    """
    service = WhatsAppQuoteService(db)
    op = service.get_by_uuid(op_uuid)
    if op is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operación no encontrada")

    allocation_svc = ProfitAllocationService(db)
    try:
        allocation_svc.set_allocations(
            op,
            [item.model_dump() for item in payload.allocations],
            actor=current_user,
        )
        service.resync_transaction(op)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)

    db.commit()
    db.refresh(op)
    return _profit_allocation_list(db, op)


def _profit_allocation_list(db: Session, op) -> ProfitAllocationList:
    allocation_svc = ProfitAllocationService(db)
    allocations = allocation_svc.allocations(op)
    return ProfitAllocationList(
        operation_uuid=op.uuid,
        charged_percentage=op.applied_percentage,
        allocated_percentage=round(sum(float(a.percentage or 0) for a in allocations), 4),
        unallocated_percentage=allocation_svc.unallocated_percentage(op),
        value_usdt=op.amount_usdt,
        allocations=[ProfitAllocationResponse(**a.dict()) for a in allocations],
    )
