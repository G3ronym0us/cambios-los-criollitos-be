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

from datetime import date
from typing import Literal
from uuid import UUID

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
    PaymentAllocationsUpdate,
    OutgoingSettlementsUpdate,
    WhatsAppPaymentLink,
    WhatsAppPaymentUpdate,
    WhatsAppPersonalExpense,
    ClientLoanCreate,
)
from app.core.timezones import day_bounds
from app.schemas.operation_match import PaymentSuggestionsRequest
from app.services.client_loan_service import ClientLoanService
from app.services.operation_match_service import OperationMatchService
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.schemas.fund import FundDepositFromReceipt
from app.services.fund_pending_deposit_service import FundPendingDepositService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError

router = APIRouter(prefix="/payments", tags=["Payments"])


@router.get("/outgoing/{payment_id}/loan-valuation")
async def preview_client_loan_valuation(
    payment_id: int,
    fiat_currency: str | None = Query(None, min_length=2, max_length=10),
    payment_currency: str | None = Query(None, min_length=2, max_length=10),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Equivalencias del pago usando las tasas registradas en la fecha del comprobante."""
    try:
        return ClientLoanService(db).preview_outgoing(payment_id, fiat_currency, payment_currency)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


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
            payment_currency=payload.payment_currency,
            fiat_currency=payload.fiat_currency,
            fiat_amount=payload.fiat_amount,
            usdt_amount=payload.usdt_amount,
            bcv_amount=payload.bcv_amount,
            notes=payload.notes,
            created_by_user_id=current_user.id,
            client_uuid=payload.client_uuid,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{table}/{payment_id}/fund-deposit")
async def suggest_fund_deposit(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Qué fondo y qué gestor proponer para registrar este comprobante como depósito.
    El fondo sale del canal donde llegó; el gestor, de quién lo mandó.
    """
    try:
        return FundPendingDepositService(db).suggest_for_receipt(table, payment_id)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{table}/{payment_id}/fund-deposit", status_code=201)
async def create_fund_deposit_from_receipt(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    payload: FundDepositFromReceipt,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    El comprobante ES el depósito: queda PENDING con él enganchado como evidencia, y se
    confirma en Fondos → Depósitos pendientes como cualquier otro.
    """
    try:
        return FundPendingDepositService(db).create_from_receipt(
            table, payment_id, payload.group_uuid, payload.user_uuid, current_user.id,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{table}/stats")
async def get_payments_stats(
    table: Literal["incoming", "outgoing"],
    search: str | None = Query(None),
    out_class: str = Query("ALL"),
    # `date`, no `datetime`: el front manda días de calendario (yyyy-mm-dd) y pydantic
    # rechaza esa forma si el tipo es datetime.
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Agregados de la bandeja: cuántos comprobantes esperan una decisión, cuánto dinero de
    ellos no respalda todavía ninguna operación, y el ritmo del día.

    Acepta los mismos filtros que el listado salvo `attention`: la cifra de «por atender»
    es justamente la que ese filtro selecciona.
    """
    service = WhatsAppPaymentService(db)
    start, end = day_bounds(date_from, date_to)
    try:
        return service.payments_stats(
            table,
            search=search,
            out_class=out_class,
            date_from=start,
            date_to=end,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/{table}/suggestions")
async def suggest_operations_for_payments(
    table: Literal["incoming", "outgoing"],
    payload: PaymentSuggestionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Operación sugerida para una tanda de comprobantes, para que el listado la muestre en
    cada fila sin pedirla una por una. Misma puntuación que el selector de vincular.
    """
    service = OperationMatchService(db)
    return {"items": service.suggest_for_payments(payload.payment_ids, table)}


@router.get("/{table}")
async def list_payments(
    table: Literal["incoming", "outgoing"],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None),
    out_class: str = Query("ALL"),
    unlinked_only: bool = Query(False),
    attention: Literal["ALL", "ATTENTION", "RECONCILED"] = Query("ALL"),
    # `date`, no `datetime`: el front manda días de calendario (yyyy-mm-dd) y pydantic
    # rechaza esa forma si el tipo es datetime.
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Página de pagos (paginada + búsqueda/clasificación server-side). Devuelve {items, total}."""
    service = WhatsAppPaymentService(db)
    start, end = day_bounds(date_from, date_to)
    try:
        return service.list_payments_page(
            table,
            limit,
            offset,
            search,
            out_class,
            unlinked_only,
            attention=attention,
            date_from=start,
            date_to=end,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.patch("/{table}/{payment_id}")
async def update_payment(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    payload: WhatsAppPaymentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Corrige a mano los campos que el OCR leyó mal (monto, moneda, referencia, bancos...).
    Guarda el valor previo en `correction_original` y marca `corrected_at`, igual que la
    corrección que hace el bot: el operador ve después qué había leído la máquina.

    Solo se aplican los campos presentes en el body (`exclude_unset`).
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.update_payment(table, payment_id, payload.dict(exclude_unset=True))
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
    Vincula un pago; si es saliente, completa la operación cuando lo entregado cubre su valor.
    Con `settled_amount` (solo salientes) se fija cuánto de ese valor cubre el comprobante.

    Al DESVINCULAR (`operation_uuid` null) el último comprobante de una operación hace falta
    `orphan_action`: sin él responde 409 para que el operador decida (ver `unlink-preview`).
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_operation(
            table,
            payment_id,
            payload.operation_uuid,
            completing_user=current_user,
            complete_outgoing=True,
            orphan_action=payload.orphan_action,
            orphan_note=payload.orphan_note,
            settled_amount=payload.settled_amount,
        )
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/outgoing/{payment_id}/coverage")
async def preview_outgoing_coverage(
    payment_id: int,
    operation_uuid: UUID = Query(..., description="Operación a la que se vincularía"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Cuánto del valor de la operación cubriría este comprobante: lo que da la tasa, lo que le
    falta al trato, y —si se decide que lo cubre entero— a qué tasa quedaría y cuánto se
    aparta de la de referencia. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.coverage_preview(payment_id, operation_uuid)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/incoming/{payment_id}/allocations")
async def get_payment_allocations(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reparto de un pago entrante: qué operaciones cubre, con cuánto y cómo se pagó cada una.
    Lo que sobra sale como `unassigned`. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.allocation_summary(payment_id)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.put("/incoming/{payment_id}/allocations")
async def set_payment_allocations(
    payment_id: int,
    payload: PaymentAllocationsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reparte un pago entrante entre varias operaciones (un Zelle de 220 puede cubrir 200 de un
    cambio a BRL y 20 de otro a VES). Reemplaza el reparto anterior. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_allocations(payment_id, payload.allocations, actor=current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/outgoing/{payment_id}/settlements")
async def get_outgoing_settlements(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reparto de un comprobante de salida: qué operaciones cubre y con cuánto del valor de cada
    una. Un cliente que manda dos Zelle y recibe un solo pago cubre dos tratos con un
    comprobante. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.settlement_summary(payment_id)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.put("/outgoing/{payment_id}/settlements")
async def set_outgoing_settlements(
    payment_id: int,
    payload: OutgoingSettlementsUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Reparte un comprobante de salida entre varias operaciones. Reemplaza el reparto anterior
    y completa las que queden cubiertas. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.set_settlements(payment_id, payload.settlements, actor=current_user)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.get("/{table}/{payment_id}/unlink-preview")
async def preview_payment_unlink(
    table: Literal["incoming", "outgoing"],
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Qué dejaría atrás desvincular este pago: si su operación se quedaría sin comprobantes y,
    en ese caso, la transacción y los movimientos de fondo que se irían con ella. Operador JWT.
    """
    service = WhatsAppPaymentService(db)
    try:
        return service.unlink_preview(table, payment_id)
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
            payment_id,
            payload.is_personal_expense,
            payload.personal_description,
            actor=current_user,
            orphan_action=payload.orphan_action,
            orphan_note=payload.orphan_note,
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
            payment_id,
            payload.is_irrelevant,
            payload.irrelevant_description,
            actor=current_user,
            orphan_action=payload.orphan_action,
            orphan_note=payload.orphan_note,
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


@router.post("/outgoing/{payment_id}/convert-to-incoming")
async def convert_outgoing_to_incoming(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mueve un pago saliente a la bandeja de entrantes sin exigir un grupo."""
    try:
        return WhatsAppPaymentService(db).convert_outgoing_to_incoming(payment_id)
    except QuoteServiceError as exc:
        raise HTTPException(status_code=exc.http_status, detail=exc.message)


@router.post("/incoming/{payment_id}/convert-to-outgoing")
async def convert_incoming_to_outgoing(
    payment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Devuelve un pago entrante no contabilizado a la bandeja de salientes."""
    try:
        return WhatsAppPaymentService(db).convert_incoming_to_outgoing(payment_id)
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
            fund_group_provided="fund_group_uuid" in payload.model_fields_set,
            notes=payload.notes,
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


