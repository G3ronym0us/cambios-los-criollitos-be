"""
Servicio de pagos de WhatsApp (comprobantes OCR). Espejo de las funciones de
`whatsapp-bot/src/operations.ts` (save/list/update/link/personal/irrelevant/
create-op-from-payment/corrected).

El emparejamiento vive en `operation_match_service.py`; aquí persistimos comprobantes y
resolvemos sus vínculos con las operaciones.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session, joinedload, selectinload

from app.core.timezones import CARACAS_TZ
from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.fund import FundGroup, FundMovement, FundMovementType
from app.models.user import User
from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from app.models.whatsapp_payment import (
    WhatsAppIncomingPayment,
    WhatsAppOutgoingPayment,
    WhatsAppPaymentAllocation,
)
from app.models.client_loan import ClientLoan
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.fund_repository import FundRepository
from app.services import valuation
from app.schemas.whatsapp import WhatsAppOperationComplete
from app.services.operation_match_service import receipt_fingerprint
from app.services.whatsapp_client_account_service import WhatsAppClientAccountService
from app.services.whatsapp_quote_service import (
    QuoteServiceError,
    WhatsAppQuoteService,
    is_unassigned_client_phone,
)
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


EDITABLE_FIELDS = ["provider", "amount", "currency", "bank_from", "bank_to", "identification", "phone_to", "reference"]
QUOTE_TTL_MINUTES = 30

# ZELLE y PAYPAL son métodos de pago en dólares, no monedas propias: para la contabilidad de
# fondos se liquidan como USD (un fondo en USD debe matchear un lado ZELLE/PAYPAL de la op).
_SETTLEMENT_CURRENCY = {"ZELLE": "USD", "PAYPAL": "USD"}


def settlement_currency(symbol: Optional[str]) -> str:
    up = (symbol or "").upper()
    return _SETTLEMENT_CURRENCY.get(up, up)


def _settlement_sql(column):
    """`settlement_currency` en SQL, para comparar monedas dentro de una consulta."""
    upper = func.upper(column)
    return case((upper.in_(tuple(_SETTLEMENT_CURRENCY)), "USD"), else_=upper)


# Redondeos de OCR y de tasa: por debajo de esto un pago se considera cuadrado.
AMOUNT_EPSILON = 0.01

# Los tres estados del filtro de la bandeja.
ATTENTION_FILTERS = ("ALL", "ATTENTION", "RECONCILED")


def _implicit_allocation_dict(op, amount: float) -> dict:
    """
    El reparto que el vínculo directo implica, con la misma forma que
    `WhatsAppPaymentAllocation.dict()` pero sin fila detrás: `uuid` viene en None para que
    quien lo consuma sepa que todavía no está materializado.
    """
    cp = op.currency_pair
    return {
        "uuid": None,
        "amount": round(amount, 2),
        "operation_uuid": op.uuid,
        "operation_status": op.status.value if op.status else None,
        "pair_symbol": cp.pair_symbol if cp else None,
        "from_amount": op.from_amount,
        "from_currency": cp.from_currency.symbol if cp and cp.from_currency else None,
        "to_amount": op.to_amount,
        "to_currency": cp.to_currency.symbol if cp and cp.to_currency else None,
        "rate_used": op.rate_used,
        "created_by_username": None,
        "created_at": None,
    }


class WhatsAppPaymentService:
    def __init__(self, db: Session):
        self.db = db
        self.pair_repo = CurrencyPairRepository(db)

    # ---------- Helpers ----------

    def _model(self, table: str):
        if table == "incoming":
            return WhatsAppIncomingPayment
        if table == "outgoing":
            return WhatsAppOutgoingPayment
        raise QuoteServiceError("invalid_table", f"Tabla inválida: {table}", 400)

    def _get_or_404(self, table: str, payment_id: int):
        Model = self._model(table)
        row = self.db.query(Model).filter(Model.id == payment_id).first()
        if row is None:
            raise QuoteServiceError("not_found", f"Pago {table}/{payment_id} no encontrado", 404)
        return row

    def _resolve_op_id(self, operation_uuid: Optional[UUID]) -> Optional[int]:
        if operation_uuid is None:
            return None
        op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == operation_uuid).first()
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operation {operation_uuid} no encontrada", 404)
        return op.id

    def _client_ref(self, phone: str) -> tuple[Optional[str], Optional[str]]:
        """Devuelve (display_name, uuid) del cliente con ese teléfono, o (None, None)."""
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
        if client is None:
            return None, None
        return client.display_name, str(client.uuid)

    def _with_name(self, payment) -> dict:
        d = payment.dict()
        name, client_uuid = self._client_ref(payment.client_phone)
        d["client_name"] = name
        d["client_uuid"] = client_uuid
        return d

    def _assert_not_loan(self, payment_id: int) -> None:
        loan = self.db.query(ClientLoan.id).filter(ClientLoan.outgoing_payment_id == payment_id).first()
        if loan is not None:
            raise QuoteServiceError(
                "payment_is_loan",
                "Este pago está registrado como préstamo y no puede recibir otra clasificación",
                409,
            )

    # ---------- Crear / listar ----------

    def create_payment(self, table: str, payload) -> dict:
        Model = self._model(table)
        if table == "incoming":
            repeated, side = self._already_received(payload)
            if repeated is not None:
                out = self._with_name(repeated)
                out["duplicate_of_id"] = repeated.id
                out["duplicate_side"] = side
                return out
        op_id = self._resolve_op_id(payload.operation_uuid)
        kwargs = dict(
            client_phone=payload.client_phone,
            raw_text=payload.raw_text,
            provider=payload.provider,
            amount=payload.amount,
            currency=payload.currency,
            bank_from=payload.bank_from,
            bank_to=payload.bank_to,
            account_number=payload.account_number,
            identification=payload.identification,
            phone_to=payload.phone_to,
            reference=payload.reference,
            whatsapp_operation_id=op_id,
        )
        if table == "outgoing":
            kwargs["source_payment_id"] = payload.source_payment_id
        row = Model(**kwargs)
        self.db.add(row)
        # Garantiza un registro de cliente para el teléfono del pago (no para JIDs de
        # grupo): así todo número con pagos tiene perfil. Sin display_name → la UI lo
        # muestra como el propio número hasta que el operador le ponga nombre.
        if not payload.client_phone.endswith("@g.us"):
            WhatsAppQuoteService(self.db).upsert_client(payload.client_phone)
        self.db.commit()
        self.db.refresh(row)
        # El intermediario nunca mandó los datos, pero el pago ya se hizo: el comprobante
        # es la mejor fuente posible de la cuenta del beneficiario. El bot suele crear el
        # saliente ya vinculado a la op (`operation_uuid` en el payload), sin pasar nunca
        # por set_operation, así que este es el único disparador para ese camino.
        if table == "outgoing" and row.whatsapp_operation_id is not None:
            target_op = (
                self.db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.id == row.whatsapp_operation_id)
                .first()
            )
            if target_op is not None:
                WhatsAppClientAccountService(self.db).learn_from_outgoing(target_op, row)
        out = self._with_name(row)
        if table == "incoming":
            out["duplicate_of_id"] = None
            out["duplicate_side"] = None
        return out

    # Ventana en la que dos capturas iguales del mismo cliente se consideran la misma. Es
    # generosa porque el parecido no es de monto: es el texto exacto de una misma imagen.
    DUPLICATE_WINDOW = timedelta(days=7)

    #: La misma huella que usa el matcher de reenvíos al grupo, para que "es la misma captura"
    #: signifique lo mismo en los dos sitios.
    _receipt_fingerprint = staticmethod(receipt_fingerprint)

    def _already_received(self, payload) -> tuple[Optional[object], Optional[str]]:
        """
        El comprobante que este cliente ya había mandado, si es que este es un reenvío, junto
        al lado en el que estaba: `"incoming"` si lo mandó él, `"outgoing"` si es el que le
        mandamos nosotros.

        Un cliente que vuelve a mandar la captura que ya mandó no está pagando dos veces, pero
        antes cada reenvío se guardaba como un pago nuevo y, al no calzar con ninguna operación
        libre, el monto se trataba como una cotización nueva: un trato fantasma por dinero que
        nunca se movió.

        El lado SALIENTE cuenta igual (caso Dionis, 2026-08-01): el intermediario reenvía el
        comprobante que le pasamos —a veces de días atrás— para señalar a quién hay que pagarle
        esta vez, y su monto no tiene nada que ver con el nuevo encargo. Sin esto se guardaba un
        entrante fantasma por dinero que nunca entró y, peor, el bot cotizaba ese monto.

        Se reconoce por la referencia del banco cuando la hay —identifica la transferencia— y
        si no, por el texto del OCR junto al monto: dos pagos distintos producen capturas
        distintas (llevan su hora), así que un texto idéntico es la misma imagen otra vez.
        """
        since = datetime.now(timezone.utc) - self.DUPLICATE_WINDOW
        reference = (payload.reference or "").strip()
        fingerprint = self._receipt_fingerprint(payload.raw_text)

        for Model, side in ((WhatsAppIncomingPayment, "incoming"), (WhatsAppOutgoingPayment, "outgoing")):
            base = self.db.query(Model).filter(
                Model.client_phone == payload.client_phone,
                Model.created_at >= since,
            )
            if reference:
                previous = (
                    base.filter(Model.reference.ilike(reference))
                    .order_by(Model.id.desc())
                    .first()
                )
                if previous is not None:
                    return previous, side
                continue
            if not fingerprint:
                continue
            for previous in base.order_by(Model.id.desc()).limit(50).all():
                if (
                    previous.amount == payload.amount
                    and self._receipt_fingerprint(previous.raw_text) == fingerprint
                ):
                    return previous, side
        return None, None

    def _payments_base_query(self, Model):
        """Query base con joins a cliente (display_name/uuid) y a la op (status)."""
        return (
            self.db.query(
                Model,
                WhatsAppClient.display_name,
                WhatsAppClient.uuid,
                WhatsAppOperation.status,
            )
            .outerjoin(WhatsAppClient, WhatsAppClient.phone == Model.client_phone)
            .outerjoin(WhatsAppOperation, WhatsAppOperation.id == Model.whatsapp_operation_id)
        )

    @staticmethod
    def _row_to_dict(payment, display_name, client_uuid, op_status) -> dict:
        d = payment.dict()
        d["client_name"] = display_name
        d["client_uuid"] = str(client_uuid) if client_uuid else None
        d["operation_status"] = op_status.value if op_status else None
        return d

    def list_payments(self, table: str, limit: int = 200) -> list[dict]:
        """Lista simple (sin paginar). La usa el bot vía /whatsapp/payments/{table}."""
        Model = self._model(table)
        rows = self._payments_base_query(Model).order_by(Model.created_at.desc()).limit(limit).all()
        return [self._row_to_dict(*r) for r in rows]

    def _attention_condition(self, Model, table: str):
        """
        Qué cuenta como «por atender»: comprobantes cuyo dinero todavía no respalda nada.

        Es la misma pregunta que responde el listado fila por fila, pero expresada en SQL
        para poder filtrar y contar sin traerse la tabla entera. Un entrante está por
        atender si el OCR no leyó el monto, si no tiene destino de ningún tipo (ni
        operación, ni depósito a fondo, ni crédito de saldo), o si tiene destino pero le
        sobra dinero. Un saliente, si no está clasificado y además le falta el monto o el
        vínculo con su operación.
        """
        if table == "outgoing":
            # Clasificar es una decisión terminal: personal, irrelevante o préstamo ya dicen
            # dónde acabó ese dinero. Va PRIMERO porque antes mandaba `amount IS NULL` y un
            # comprobante marcado irrelevante seguía contando como «por atender» para
            # siempre: el OCR no va a leer un monto nuevo de una foto que el operador ya
            # descartó, así que la fila no salía nunca de la bandeja (caso saliente #4691).
            classified = or_(
                Model.is_personal_expense.is_(True),
                Model.is_irrelevant.is_(True),
                exists().where(ClientLoan.outgoing_payment_id == Model.id),
            )
            return and_(
                ~classified,
                or_(
                    Model.amount.is_(None),
                    Model.whatsapp_operation_id.is_(None),
                ),
            )

        # `correlate(Model)` es obligatorio: la query del listado ya trae unida la operación,
        # y sin esto SQLAlchemy correlaciona también esa tabla y deja la subconsulta sin FROM.
        allocated = (
            select(func.coalesce(func.sum(WhatsAppPaymentAllocation.amount), 0.0))
            .where(WhatsAppPaymentAllocation.incoming_payment_id == Model.id)
            .correlate(Model)
            .scalar_subquery()
        )
        credited = (
            select(func.coalesce(func.sum(WhatsAppBalanceEntry.amount), 0.0))
            .where(
                WhatsAppBalanceEntry.incoming_payment_id == Model.id,
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
            )
            .correlate(Model)
            .scalar_subquery()
        )
        has_allocation = (
            select(WhatsAppPaymentAllocation.id)
            .where(WhatsAppPaymentAllocation.incoming_payment_id == Model.id)
            .correlate(Model)
            .exists()
        )
        has_deposit = (
            select(FundMovement.id)
            .where(FundMovement.incoming_payment_id == Model.id)
            .correlate(Model)
            .exists()
        )

        # Vínculo directo sin filas de reparto: el implícito de `_default_allocation_amount`.
        # Solo puede sobrar dinero si la op liquida en la moneda del pago; si no son
        # comparables se le asigna todo y no hay nada pendiente.
        op_from_amount = (
            select(WhatsAppOperation.from_amount)
            .where(WhatsAppOperation.id == Model.whatsapp_operation_id)
            .correlate(Model)
            .scalar_subquery()
        )
        op_from_symbol = (
            select(Currency.symbol)
            .select_from(WhatsAppOperation)
            .join(CurrencyPair, CurrencyPair.id == WhatsAppOperation.currency_pair_id)
            .join(Currency, Currency.id == CurrencyPair.from_currency_id)
            .where(WhatsAppOperation.id == Model.whatsapp_operation_id)
            .correlate(Model)
            .scalar_subquery()
        )

        return or_(
            Model.amount.is_(None),
            and_(
                Model.whatsapp_operation_id.is_(None),
                ~has_deposit,
                ~has_allocation,
                credited <= 0,
            ),
            and_(
                Model.amount.isnot(None),
                has_allocation,
                allocated + credited < Model.amount - AMOUNT_EPSILON,
            ),
            and_(
                Model.amount.isnot(None),
                Model.currency.isnot(None),
                Model.whatsapp_operation_id.isnot(None),
                ~has_allocation,
                op_from_amount.isnot(None),
                op_from_symbol.isnot(None),
                _settlement_sql(op_from_symbol) == _settlement_sql(Model.currency),
                op_from_amount + credited < Model.amount - AMOUNT_EPSILON,
            ),
        )

    def _filtered_payments_query(
        self,
        table: str,
        *,
        search: Optional[str] = None,
        out_class: str = "ALL",
        unlinked_only: bool = False,
        attention: str = "ALL",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ):
        """Query base + todos los filtros de la bandeja. La comparten listado y stats."""
        Model = self._model(table)
        q = self._payments_base_query(Model)

        # Los selectores de una operación solo deben recibir pagos disponibles.
        # Aplicarlo en la consulta evita exponer pagos pertenecientes a otra
        # operación y mantiene correcto el total paginado.
        if unlinked_only:
            q = q.filter(Model.whatsapp_operation_id.is_(None))
            if table == "outgoing":
                q = q.filter(
                    ~exists().where(ClientLoan.outgoing_payment_id == Model.id)
                )

        search = (search or "").strip()
        if search:
            like = f"%{search}%"
            q = q.filter(
                or_(
                    WhatsAppClient.display_name.ilike(like),
                    Model.client_phone.ilike(like),
                    Model.bank_from.ilike(like),
                    Model.bank_to.ilike(like),
                    Model.reference.ilike(like),
                    Model.provider.ilike(like),
                )
            )

        # Clasificación solo aplica a salientes (las columnas existen únicamente ahí).
        if table == "outgoing" and out_class and out_class != "ALL":
            if out_class == "PERSONAL":
                q = q.filter(Model.is_personal_expense.is_(True))
            elif out_class == "IRRELEVANT":
                q = q.filter(Model.is_irrelevant.is_(True))
            elif out_class == "OPERATIONAL":
                q = q.filter(
                    Model.is_personal_expense.is_(False),
                    Model.is_irrelevant.is_(False),
                    ~exists().where(ClientLoan.outgoing_payment_id == Model.id),
                )
            elif out_class == "UNLINKED":
                q = q.filter(
                    Model.is_personal_expense.is_(False),
                    Model.is_irrelevant.is_(False),
                    Model.whatsapp_operation_id.is_(None),
                    ~exists().where(ClientLoan.outgoing_payment_id == Model.id),
                )
            elif out_class == "LOAN":
                q = q.filter(exists().where(ClientLoan.outgoing_payment_id == Model.id))

        # Rango semiabierto [date_from, date_to). El router traduce el día final elegido a la
        # medianoche siguiente, así que aquí `date_to` ya es el borde exclusivo.
        if date_from:
            q = q.filter(Model.created_at >= date_from)
        if date_to:
            q = q.filter(Model.created_at < date_to)

        if attention == "ATTENTION":
            q = q.filter(self._attention_condition(Model, table))
        elif attention == "RECONCILED":
            q = q.filter(~self._attention_condition(Model, table))

        return q

    def list_payments_page(
        self,
        table: str,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        out_class: str = "ALL",
        unlinked_only: bool = False,
        attention: str = "ALL",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """Página de pagos para el front: búsqueda + clasificación server-side. Devuelve {items, total}."""
        Model = self._model(table)
        q = self._filtered_payments_query(
            table,
            search=search,
            out_class=out_class,
            unlinked_only=unlinked_only,
            attention=attention,
            date_from=date_from,
            date_to=date_to,
        )

        total = q.count()
        rows = q.order_by(Model.created_at.desc()).limit(limit).offset(offset).all()
        items = [self._row_to_dict(*r) for r in rows]
        if table == "incoming" and items:
            self._attach_deposits(items)
            self._attach_allocations(items)
        if table == "outgoing" and items:
            self._attach_loans(items)
        return {"items": items, "total": total}

    def payments_stats(
        self,
        table: str,
        *,
        search: Optional[str] = None,
        out_class: str = "ALL",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        scan_limit: int = 1000,
    ) -> dict:
        """
        Agregados de la franja de atención: cuántos comprobantes esperan una decisión,
        cuánto dinero de ellos no respalda nada, y el ritmo del día.

        Respeta los mismos filtros que el listado salvo el propio de atención — es lo que
        permite que la tarjeta funcione como acceso al filtro.

        El monto sin asignar no se puede sumar en SQL (el reparto implícito depende del par
        de la operación), así que se recorre solo el conjunto por atender, acotado a
        `scan_limit` filas.
        """
        Model = self._model(table)
        base = self._filtered_payments_query(
            table, search=search, out_class=out_class, date_from=date_from, date_to=date_to
        )
        attention_clause = self._attention_condition(Model, table)
        attention_q = base.filter(attention_clause)

        needs_attention = attention_q.count()

        unassigned: list[dict] = []
        truncated = False
        if table == "incoming" and needs_attention:
            rows = (
                attention_q.order_by(Model.created_at.desc()).limit(scan_limit).all()
            )
            truncated = needs_attention > len(rows)
            items = [self._row_to_dict(*r) for r in rows]
            self._attach_allocations(items)
            by_currency: dict[str, dict] = {}
            for it in items:
                amount = it.get("unassigned_amount") or 0
                if amount <= AMOUNT_EPSILON:
                    continue
                symbol = (it.get("currency") or "?").upper()
                slot = by_currency.setdefault(
                    symbol, {"currency": symbol, "amount": 0.0, "count": 0}
                )
                slot["amount"] = round(slot["amount"] + amount, 2)
                slot["count"] += 1
            unassigned = sorted(by_currency.values(), key=lambda s: -s["amount"])

        start_of_day = datetime.now(CARACAS_TZ).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        today_q = base.filter(Model.created_at >= start_of_day)
        received_today = today_q.count()
        reconciled_today = today_q.filter(~attention_clause).count()

        return {
            "table": table,
            "needs_attention": needs_attention,
            "unassigned": unassigned,
            "unassigned_truncated": truncated,
            "received_today": received_today,
            "reconciled_today": reconciled_today,
        }

    def _attach_loans(self, items: list[dict]) -> None:
        ids = [item["id"] for item in items]
        loans = (
            self.db.query(ClientLoan)
            .options(joinedload(ClientLoan.repayments))
            .filter(ClientLoan.outgoing_payment_id.in_(ids))
            .all()
        )
        by_payment = {loan.outgoing_payment_id: loan.payment_summary() for loan in loans}
        for item in items:
            item["loan"] = by_payment.get(item["id"])

    def _attach_deposits(self, items: list[dict]) -> None:
        """Agrega a cada pago entrante un bloque `deposit` (o None) si tiene un FundMovement asociado."""
        ids = [it["id"] for it in items]
        rows = (
            self.db.query(FundMovement, FundGroup.name)
            .outerjoin(FundGroup, FundGroup.id == FundMovement.group_id)
            .filter(FundMovement.incoming_payment_id.in_(ids))
            .all()
        )
        by_payment = {
            mv.incoming_payment_id: {
                "uuid": mv.uuid,
                "method": mv.deposit_method,
                "amount": mv.amount,
                "currency": mv.currency,
                "group_name": group_name,
            }
            for mv, group_name in rows
        }
        for it in items:
            it["deposit"] = by_payment.get(it["id"])

    def _attach_allocations(self, items: list[dict]) -> None:
        """
        Agrega a cada entrante cuánto de él está repartido entre operaciones y cuánto queda
        sin asignar: es el aviso de "el pago dice 220 y la op 200" en el listado.

        Un pago con vínculo directo y sin reparto explícito NO está sin asignar: el FK es la
        relación primaria y vale por sí sola (ver `_sync_primary_operation`). Es además el
        estado normal de todo lo que crea el bot, así que leer solo la tabla de reparto hacía
        que un pago perfectamente colocado se anunciara como "60 sin asignar".
        """
        ids = [it["id"] for it in items]
        rows = (
            self.db.query(WhatsAppPaymentAllocation)
            .filter(WhatsAppPaymentAllocation.incoming_payment_id.in_(ids))
            .all()
        )
        assigned: dict[int, float] = {}
        counts: dict[int, int] = {}
        for row in rows:
            assigned[row.incoming_payment_id] = assigned.get(row.incoming_payment_id, 0) + row.amount
            counts[row.incoming_payment_id] = counts.get(row.incoming_payment_id, 0) + 1

        # Los que no tienen reparto: se carga en lote el pago con su op y su par para poder
        # derivar el implícito sin una consulta por fila.
        implicit_ids = [i for i in ids if i not in assigned]
        implicit: dict[int, float] = {}
        if implicit_ids:
            payments = (
                self.db.query(WhatsAppIncomingPayment)
                .options(
                    selectinload(WhatsAppIncomingPayment.operation).selectinload(
                        WhatsAppOperation.currency_pair
                    )
                )
                .filter(WhatsAppIncomingPayment.id.in_(implicit_ids))
                .all()
            )
            for payment in payments:
                if payment.operation is not None:
                    implicit[payment.id] = self._default_allocation_amount(
                        payment, payment.operation
                    )

        # Lo acreditado al saldo, en una sola consulta: pedirlo por fila hacía 50 viajes por
        # página, y `payments_stats` recorre bastantes más.
        credited_by_payment: dict[int, float] = {}
        credited_rows = (
            self.db.query(
                WhatsAppBalanceEntry.incoming_payment_id,
                func.coalesce(func.sum(WhatsAppBalanceEntry.amount), 0.0),
            )
            .filter(
                WhatsAppBalanceEntry.incoming_payment_id.in_(ids),
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
            )
            .group_by(WhatsAppBalanceEntry.incoming_payment_id)
            .all()
        )
        for payment_id, amount in credited_rows:
            credited_by_payment[payment_id] = round(amount, 2)

        for it in items:
            total = round(it.get("amount") or 0, 2)
            done = round(assigned.get(it["id"], implicit.get(it["id"], 0)), 2)
            credited = credited_by_payment.get(it["id"], 0.0) if total else 0.0
            it["allocated_amount"] = done
            # El implícito no es una fila: el contador sigue siendo el de repartos explícitos.
            it["allocations_count"] = counts.get(it["id"], 0)
            it["credited_to_balance"] = credited
            it["unassigned_amount"] = round(total - done - credited, 2) if total else 0.0

    def list_payments_for_operation(self, op_uuid: UUID) -> dict:
        """
        Pagos entrantes + salientes de una operación (para el detalle de la op). Del lado
        entrante entran también los pagos que la respaldan por reparto sin ser su op principal.
        """
        op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_uuid).first()
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operation {op_uuid} no encontrada", 404)
        allocated_ids = [
            row[0]
            for row in self.db.query(WhatsAppPaymentAllocation.incoming_payment_id)
            .filter(WhatsAppPaymentAllocation.whatsapp_operation_id == op.id)
            .all()
        ]
        inc_rows = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(
                or_(
                    WhatsAppIncomingPayment.whatsapp_operation_id == op.id,
                    WhatsAppIncomingPayment.id.in_(allocated_ids) if allocated_ids else False,
                )
            )
            .order_by(WhatsAppIncomingPayment.created_at.asc())
            .all()
        )
        out_rows = (
            self.db.query(WhatsAppOutgoingPayment)
            .filter(WhatsAppOutgoingPayment.whatsapp_operation_id == op.id)
            .order_by(WhatsAppOutgoingPayment.created_at.asc())
            .all()
        )
        incoming = [self._with_name(p) for p in inc_rows]
        outgoing = [self._with_name(p) for p in out_rows]
        if incoming:
            self._attach_deposits(incoming)
            self._attach_allocations(incoming)
            # Cuánto de cada entrante corresponde a ESTA op (puede ser solo una parte).
            by_payment = {
                a.incoming_payment_id: a.amount
                for a in self.db.query(WhatsAppPaymentAllocation)
                .filter(WhatsAppPaymentAllocation.whatsapp_operation_id == op.id)
                .all()
            }
            for item in incoming:
                item["allocated_to_operation"] = by_payment.get(item["id"])
        return {"incoming": incoming, "outgoing": outgoing}

    # ---------- Editar (correction tracking) ----------

    def update_payment(self, table: str, payment_id: int, fields: dict) -> dict:
        row = self._get_or_404(table, payment_id)
        keys = [k for k in fields if k in EDITABLE_FIELDS and fields[k] is not None]
        if not keys:
            return self._with_name(row)

        # Snapshot pre-edit (preserva el primer snapshot si ya fue corregido).
        snapshot = {}
        if row.corrected_at and row.correction_original:
            snapshot = json.loads(row.correction_original)
        for k in keys:
            if k not in snapshot:
                snapshot[k] = getattr(row, k)

        for k in keys:
            setattr(row, k, fields[k])
        row.corrected_at = datetime.utcnow()
        row.correction_original = json.dumps(snapshot, default=str)
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

    # ---------- Vincular / flags ----------

    def _payments_left_without(self, op_id: int, table: str, payment_id: int) -> int:
        """
        Comprobantes que le quedarían a la operación si este pago se desvincula. Del lado
        entrante cuenta el reparto (`whatsapp_payment_allocations`) además del FK: un pago
        puede respaldar a una op sin ser su op principal.
        """
        incoming_ids = {
            row[0]
            for row in self.db.query(WhatsAppIncomingPayment.id)
            .filter(WhatsAppIncomingPayment.whatsapp_operation_id == op_id)
            .all()
        } | {
            row[0]
            for row in self.db.query(WhatsAppPaymentAllocation.incoming_payment_id)
            .filter(WhatsAppPaymentAllocation.whatsapp_operation_id == op_id)
            .all()
        }
        outgoing = self.db.query(WhatsAppOutgoingPayment.id).filter(
            WhatsAppOutgoingPayment.whatsapp_operation_id == op_id
        )
        if table == "incoming":
            incoming_ids.discard(payment_id)
        else:
            outgoing = outgoing.filter(WhatsAppOutgoingPayment.id != payment_id)
        return len(incoming_ids) + outgoing.count()

    # ---------- Cuánto cubre un saliente del valor de la operación ----------

    def operation_value(self, op: WhatsAppOperation) -> tuple[float, str]:
        """Valor del trato y su moneda. Cae al lado origen de la cotización si aún no lo tiene."""
        if op.amount:
            return float(op.amount), (op.currency or "")
        cp = op.currency_pair
        from_symbol = cp.from_currency.symbol if cp and cp.from_currency else ""
        return float(op.from_amount or 0), from_symbol

    def delivered_amount(self, op: WhatsAppOperation, exclude_payment_id: Optional[int] = None) -> float:
        """Suma de lo que cubren los comprobantes de salida de la operación."""
        q = self.db.query(WhatsAppOutgoingPayment).filter(
            WhatsAppOutgoingPayment.whatsapp_operation_id == op.id,
            WhatsAppOutgoingPayment.settled_amount.isnot(None),
        )
        if exclude_payment_id is not None:
            q = q.filter(WhatsAppOutgoingPayment.id != exclude_payment_id)
        return round(sum(p.settled_amount for p in q.all()), 2)

    def _reference_rate(self, op: WhatsAppOperation, payment) -> Optional[float]:
        """
        Tasa contra la que se juzga este pago: la cotizada en la operación si su moneda de
        salida coincide con la del comprobante; si no, la activa del par correspondiente.

        Va SIEMPRE en "unidades del comprobante por unidad de valor" —la misma convención que
        `settled_rate`—, para que dividir el monto pagado entre ella dé lo que cubre del valor.
        """
        cp = op.currency_pair
        quoted_to = cp.to_currency.symbol if cp and cp.to_currency else None
        payment_currency = (payment.currency or "").upper()
        if quoted_to and payment_currency == quoted_to.upper() and op.rate_used:
            # Con inversa, `rate_used` va al revés (to = from / rate: COP por bolívar en un
            # COP-VES). Sin darlo vuelta, el comprobante cubriría una fracción del valor y la
            # operación quedaría con un pendiente fantasma.
            return (
                float(1 / op.rate_used) if op.inverse_percentage else float(op.rate_used)
            )

        _, value_currency = self.operation_value(op)
        if not payment_currency or not value_currency:
            return None
        entry = WhatsAppQuoteService(self.db).resolver.get_rate_entry_for_pair(
            value_currency, payment_currency
        )
        if entry is None or not entry.rate:
            return None
        # `rate` con inversa significa dividir: se normaliza a "unidades de destino por unidad
        # de valor" para poder dividir el monto del comprobante entre ella.
        return float(1 / entry.rate) if entry.inverse_percentage else float(entry.rate)

    def coverage_preview(self, payment_id: int, operation_uuid: UUID) -> dict:
        """
        Qué parte del valor de la operación cubriría este comprobante. Es lo que el diálogo de
        vincular necesita para proponer un monto en vez de pedirlo a ciegas.

        - `suggested`: lo que da la tasa (914,04 ÷ 4,5702 = 200)
        - `pending`: lo que le falta a la operación (220)
        - si el operador dice que cubre el pendiente, `full_effective_rate` es la tasa a la que
          realmente cambió y `full_rate_difference` cuánto se aparta de la de referencia
        """
        payment = self._get_or_404("outgoing", payment_id)
        op = (
            self.db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.uuid == str(operation_uuid))
            .first()
        )
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operación {operation_uuid} no encontrada", 404)

        value, value_currency = self.operation_value(op)
        delivered = self.delivered_amount(op, exclude_payment_id=payment.id)
        pending = round(value - delivered, 2)
        reference_rate = self._reference_rate(op, payment)

        suggested = None
        if reference_rate and payment.amount:
            suggested = round(float(payment.amount) / reference_rate, 2)

        full_effective_rate = None
        full_rate_difference = None
        full_amount_difference = None
        if pending > 0 and payment.amount:
            full_effective_rate = round(float(payment.amount) / pending, 6)
            if reference_rate:
                full_rate_difference = round(full_effective_rate - reference_rate, 6)
                # Lo que habría tocado pagar por ese pendiente a la tasa de referencia.
                full_amount_difference = round(float(payment.amount) - pending * reference_rate, 2)

        return {
            "payment": {
                "id": payment.id,
                "amount": payment.amount,
                "currency": payment.currency,
            },
            "operation_uuid": str(op.uuid),
            "value": value,
            "value_currency": value_currency,
            "delivered": delivered,
            "pending": pending,
            "reference_rate": reference_rate,
            "suggested_settled_amount": suggested,
            "full_effective_rate": full_effective_rate,
            "full_rate_difference": full_rate_difference,
            "full_amount_difference": full_amount_difference,
        }

    def _apply_settlement(
        self,
        payment,
        op: WhatsAppOperation,
        settled_amount: Optional[float],
    ) -> None:
        """Fija cuánto cubre este comprobante. Sin monto explícito, lo que da la tasa."""
        reference_rate = self._reference_rate(op, payment)
        value = settled_amount
        if value is None:
            if reference_rate and payment.amount:
                value = round(float(payment.amount) / reference_rate, 2)
            else:
                _, _ = self.operation_value(op)
                value = self.operation_value(op)[0] - self.delivered_amount(op, payment.id)
        if value is not None and value <= 0:
            raise QuoteServiceError("invalid_settled_amount", "Lo cubierto debe ser > 0", 400)
        payment.settled_amount = round(float(value), 2)
        payment.settled_reference_rate = reference_rate

    def _sync_status_from_delivery(self, op: WhatsAppOperation, completing_user: Optional[User]) -> bool:
        """
        ¿El trato quedó cubierto? Si sí y la operación estaba abierta, se completa. Devuelve
        True cuando la completó, para que el caller no la complete otra vez.
        """
        if op.status not in (WhatsAppOperationStatus.QUOTED, WhatsAppOperationStatus.PENDING):
            return False
        value, _ = self.operation_value(op)
        if value <= 0:
            return False
        # La sesión va sin autoflush: sin esto, lo que acaba de cubrir este comprobante
        # todavía no lo ve la consulta y el trato parecería incompleto.
        self.db.flush()
        if self.delivered_amount(op) + 0.01 < value:
            return False
        if completing_user is None:
            return False
        self.db.flush()
        WhatsAppQuoteService(self.db).update_status(
            op.uuid, WhatsAppOperationStatus.COMPLETED.value, completing_user
        )
        return True

    # ---------- Reparto de un entrante entre operaciones ----------

    def _allocated_total(self, payment_id: int, exclude_op_id: Optional[int] = None) -> float:
        q = self.db.query(WhatsAppPaymentAllocation).filter(
            WhatsAppPaymentAllocation.incoming_payment_id == payment_id
        )
        if exclude_op_id is not None:
            q = q.filter(WhatsAppPaymentAllocation.whatsapp_operation_id != exclude_op_id)
        return round(sum(a.amount for a in q.all()), 2)

    def _credited_to_balance(self, payment_id: int) -> float:
        """Parte del pago que se acreditó al saldo del cliente en vez de a una operación."""
        rows = (
            self.db.query(WhatsAppBalanceEntry)
            .filter(
                WhatsAppBalanceEntry.incoming_payment_id == payment_id,
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
            )
            .all()
        )
        return round(sum(e.amount for e in rows), 2)

    def _default_allocation_amount(self, payment: WhatsAppIncomingPayment, op: WhatsAppOperation) -> float:
        """
        Cuánto de este pago le toca a la operación al vincularla: lo que la op pide, o lo que
        queda del pago si es menos. Si las monedas no son comparables (la op no liquida en la
        moneda del pago) se asigna lo disponible y el operador lo ajusta.
        """
        available = round((payment.amount or 0) - self._allocated_total(payment.id, op.id), 2)
        if available <= 0:
            return 0.0
        cp = op.currency_pair
        from_symbol = cp.from_currency.symbol if cp and cp.from_currency else None
        comparable = (
            from_symbol == payment.currency
            or settlement_currency(from_symbol) == settlement_currency(payment.currency)
        )
        if comparable and op.from_amount and op.from_amount < available:
            return round(op.from_amount, 2)
        return available

    def _upsert_allocation(
        self,
        payment: WhatsAppIncomingPayment,
        op: WhatsAppOperation,
        amount: Optional[float] = None,
        actor: Optional[User] = None,
    ) -> None:
        """Crea o actualiza la parte del pago que respalda a esta operación."""
        value = amount if amount is not None else self._default_allocation_amount(payment, op)
        if value <= 0:
            return
        existing = (
            self.db.query(WhatsAppPaymentAllocation)
            .filter(
                WhatsAppPaymentAllocation.incoming_payment_id == payment.id,
                WhatsAppPaymentAllocation.whatsapp_operation_id == op.id,
            )
            .first()
        )
        if existing is not None:
            existing.amount = round(value, 2)
            return
        self.db.add(
            WhatsAppPaymentAllocation(
                incoming_payment_id=payment.id,
                whatsapp_operation_id=op.id,
                amount=round(value, 2),
                created_by_user_id=actor.id if actor else None,
            )
        )

    def _drop_allocation(self, payment_id: int, op_id: int) -> None:
        self.db.query(WhatsAppPaymentAllocation).filter(
            WhatsAppPaymentAllocation.incoming_payment_id == payment_id,
            WhatsAppPaymentAllocation.whatsapp_operation_id == op_id,
        ).delete(synchronize_session=False)

    def _sync_primary_operation(self, payment: WhatsAppIncomingPayment) -> None:
        """
        La op principal del pago (el FK de siempre, que usan el bot y el matcher) es la del
        reparto mayor. Sin reparto el FK se deja como esté: el vínculo directo sigue valiendo.
        """
        self.db.flush()
        allocations = (
            self.db.query(WhatsAppPaymentAllocation)
            .filter(WhatsAppPaymentAllocation.incoming_payment_id == payment.id)
            .order_by(WhatsAppPaymentAllocation.amount.desc(), WhatsAppPaymentAllocation.id)
            .all()
        )
        if allocations:
            payment.whatsapp_operation_id = allocations[0].whatsapp_operation_id

    def allocation_summary(self, payment_id: int) -> dict:
        """
        El reparto de un pago entrante: qué operaciones cubre, con qué monto y cómo se pagó
        cada una. Lo que sobra queda como "sin asignar", para asignarlo a otra operación o
        acreditarlo al saldo del cliente.
        """
        payment = self._get_or_404("incoming", payment_id)
        allocations = list(payment.allocations)
        credited = self._credited_to_balance(payment_id)
        total = round(payment.amount or 0, 2)

        # Sin reparto explícito pero con vínculo directo, el pago SÍ respalda a esa operación:
        # se muestra el reparto implícito para que este panel diga lo mismo que el listado.
        # No es una fila en la BD; se materializa si el operador guarda desde aquí.
        implicit: list[tuple[int, dict]] = []
        if not allocations and payment.operation is not None:
            amount = self._default_allocation_amount(payment, payment.operation)
            if amount > 0:
                implicit.append(
                    (
                        payment.operation.id,
                        _implicit_allocation_dict(payment.operation, amount),
                    )
                )

        assigned = round(
            sum(a.amount for a in allocations) + sum(d["amount"] for _, d in implicit), 2
        )

        items = []
        for op_id, item in [(a.whatsapp_operation_id, a.dict()) for a in allocations] + implicit:
            outgoing = (
                self.db.query(WhatsAppOutgoingPayment)
                .filter(WhatsAppOutgoingPayment.whatsapp_operation_id == op_id)
                .all()
            )
            item["paid_with"] = [
                {"id": o.id, "amount": o.amount, "currency": o.currency, "reference": o.reference}
                for o in outgoing
            ]
            items.append(item)

        return {
            "payment_id": payment.id,
            "amount": total,
            "currency": payment.currency,
            "client_phone": payment.client_phone,
            "assigned": assigned,
            "credited_to_balance": credited,
            "unassigned": round(total - assigned - credited, 2),
            "allocations": items,
        }

    def set_allocations(
        self,
        payment_id: int,
        items: list,
        actor: Optional[User] = None,
    ) -> dict:
        """
        Reemplaza el reparto completo del pago. Cada item es {operation_uuid, amount}.
        La suma no puede pasarse del pago (contando lo ya acreditado al saldo) y no puede
        quedar vacío: para eso se desvincula el pago, que tiene su propia confirmación.
        """
        payment = self._get_or_404("incoming", payment_id)
        if not items:
            raise QuoteServiceError(
                "allocations_empty",
                "El reparto no puede quedar vacío: desvincula el pago si ya no respalda a "
                "ninguna operación",
                400,
            )

        resolved = []
        seen = set()
        for item in items:
            op = (
                self.db.query(WhatsAppOperation)
                .filter(WhatsAppOperation.uuid == str(item.operation_uuid))
                .first()
            )
            if op is None:
                raise QuoteServiceError("op_not_found", f"Operación {item.operation_uuid} no encontrada", 404)
            if op.id in seen:
                raise QuoteServiceError(
                    "allocation_duplicated",
                    f"La operación {op.uuid} aparece dos veces en el reparto",
                    400,
                )
            if item.amount <= 0:
                raise QuoteServiceError("invalid_amount", "Cada parte del reparto debe ser > 0", 400)
            seen.add(op.id)
            resolved.append((op, round(item.amount, 2)))

        total = round(payment.amount or 0, 2)
        credited = self._credited_to_balance(payment_id)
        assigned = round(sum(amount for _, amount in resolved), 2)
        if assigned + credited > total + 0.01:
            raise QuoteServiceError(
                "allocation_exceeds_payment",
                f"El reparto suma {assigned:.2f} y con lo acreditado al saldo ({credited:.2f}) "
                f"se pasa del pago ({total:.2f})",
                400,
            )

        payment.allocations.clear()
        self.db.flush()
        for op, amount in resolved:
            self._upsert_allocation(payment, op, amount, actor)
        self._sync_primary_operation(payment)
        self.db.commit()
        self.db.refresh(payment)
        return self.allocation_summary(payment_id)

    def _resolve_orphan(
        self,
        row,
        table: str,
        payment_id: int,
        orphan_action: Optional[str],
        orphan_note: Optional[str],
        actor: Optional[User],
    ) -> Optional[WhatsAppOperation]:
        """
        Decide qué pasa con la operación cuando este pago era el único que la respaldaba.
        Devuelve la op a borrar (el borrado va después de soltar el vínculo) o None.

        Sin decisión explícita se rechaza: una op —sobre todo COMPLETED, que ya no puede
        cambiar de estado— no debe quedarse sin ningún comprobante sin que alguien lo asuma.
        """
        op = row.operation
        if op is None or self._payments_left_without(op.id, table, payment_id) > 0:
            return None

        if orphan_action == "DELETE_OPERATION":
            return op
        if orphan_action == "KEEP":
            op.no_payments_ack_by_user_id = actor.id if actor else None
            op.no_payments_ack_at = datetime.now(timezone.utc)
            op.no_payments_ack_note = orphan_note
            return None
        raise QuoteServiceError(
            "operation_would_be_orphan",
            f"Es el único pago de la operación {op.uuid}: quedaría sin ningún comprobante que "
            "la respalde. Confirma si se borra la operación con su transacción o si se "
            "mantiene así.",
            409,
        )

    def set_operation(
        self,
        table: str,
        payment_id: int,
        operation_uuid: Optional[UUID],
        completing_user: Optional[User] = None,
        complete_outgoing: bool = False,
        orphan_action: Optional[str] = None,
        orphan_note: Optional[str] = None,
        settled_amount: Optional[float] = None,
    ) -> dict:
        row = self._get_or_404(table, payment_id)
        orphaned_op = (
            self._resolve_orphan(row, table, payment_id, orphan_action, orphan_note, completing_user)
            if operation_uuid is None
            else None
        )
        if table == "outgoing" and operation_uuid is not None:
            self._assert_not_loan(payment_id)
        op = None
        if operation_uuid is not None:
            op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == operation_uuid).first()
            if op is None:
                raise QuoteServiceError("op_not_found", f"Operation {operation_uuid} no encontrada", 404)
            Model = self._model(table)
            # Una operación puede tener varios comprobantes por lado: el trato de 220 se pagó
            # con un Pix y un pago móvil, y cada uno cubre su parte del valor (`settled_amount`).
            # Lo que antes se rechazaba como duplicado ahora se contabiliza.
            if (
                complete_outgoing
                and row.whatsapp_operation_id is not None
                and row.whatsapp_operation_id != op.id
            ):
                raise QuoteServiceError(
                    "payment_already_linked",
                    "El pago ya pertenece a otra operación; desvincúlalo primero",
                    409,
                )
            # Una operación creada desde un comprobante reenviado al grupo queda sin
            # cliente conocido (anónima, o con el JID del grupo en ops antiguas). Al
            # vincular después el pago saliente real, ese número es la primera referencia
            # confiable del destinatario: adoptarlo antes de sincronizar los pagos.
            payment_client_phone = row.client_phone
            row.whatsapp_operation_id = op.id
            operation_client_phone = op.client.phone if op.client else None
            should_infer_client = (
                table == "outgoing"
                and is_unassigned_client_phone(operation_client_phone)
                and bool(payment_client_phone)
                and not is_unassigned_client_phone(payment_client_phone)
            )
            if should_infer_client:
                WhatsAppQuoteService(self.db)._assign_client(
                    op,
                    payment_client_phone,
                    display_name=None,
                    update_display_name=False,
                )
            # En el resto de los casos se conserva el criterio existente: vincular
            # el comprobante a una operación afirma que pertenece a su cliente.
            elif operation_client_phone:
                row.client_phone = operation_client_phone
            # La op vuelve a tener respaldo: el aval de "sin pago asociado" ya no aplica.
            self._clear_no_payments_ack(op)
            # El entrante estrena (o refresca) su parte del reparto para esta op.
            if table == "incoming":
                self._upsert_allocation(row, op, actor=completing_user)
        else:
            # Desvincular suelta la op principal. Si el pago repartía con otras operaciones,
            # el FK pasa a la siguiente del reparto en vez de quedarse en nada.
            if table == "incoming" and row.whatsapp_operation_id is not None:
                self._drop_allocation(row.id, row.whatsapp_operation_id)
                row.whatsapp_operation_id = None
                self._sync_primary_operation(row)
            else:
                row.whatsapp_operation_id = None

        # Un comprobante de salida cubre una parte del valor del trato. Sin monto explícito se
        # toma lo que da la tasa de referencia: el Pix de 914,04 a 4,5702 cubre 200 de 220, y
        # los 20 restantes quedan pendientes hasta que llegue otro pago.
        if table == "outgoing" and op is not None:
            self._apply_settlement(row, op, settled_amount)
            # El intermediario nunca mandó los datos, pero el pago ya se hizo: el comprobante
            # es la mejor fuente posible de la cuenta del beneficiario.
            WhatsAppClientAccountService(self.db).learn_from_outgoing(op, row)
            if complete_outgoing:
                self._sync_status_from_delivery(op, completing_user)

        # El libro sigue a la operación: si vincular este pago la completó, las patas del
        # fondo quedan al día. `_sync_status_from_delivery` ya comitea al completar la op;
        # este commit persiste lo que este sync flushee después de eso.
        if op is not None:
            self._sync_fund_legs(op, completing_user)
        self.db.commit()
        self.db.refresh(row)

        # El borrado va al final, con el vínculo ya soltado: así la op llega sin pagos a
        # delete_operation y se lleva su transacción y sus movimientos de fondo.
        if orphaned_op is not None:
            WhatsAppQuoteService(self.db).delete_operation(orphaned_op)

        return self._with_name(row)

    @staticmethod
    def _clear_no_payments_ack(op: Optional[WhatsAppOperation]) -> None:
        if op is None:
            return
        op.no_payments_ack_by_user_id = None
        op.no_payments_ack_at = None
        op.no_payments_ack_note = None

    # ---------- Editar el valor del trato ----------

    def set_operation_value(
        self,
        op_uuid: UUID,
        amount: float,
        actor: Optional[User] = None,
    ) -> dict:
        """
        Corrige cuánto vale el trato. Sube y baja: lo que faltaba era poder subirlo (la
        corrección vieja solo achicaba, y por eso una op de 200 con un comprobante de 220 no
        se podía arreglar).

        - La cotización de referencia se reescala con la misma tasa.
        - Si el valor baja por debajo de lo que le asignaron sus entrantes o cubrieron sus
          salientes, ambos se recortan y la diferencia queda sin asignar / sin cubrir.
        - El estado se recalcula con lo entregado; la transacción y el movimiento del fondo
          se re-sincronizan.
        """
        op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(op_uuid)).first()
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operación {op_uuid} no encontrada", 404)
        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El valor debe ser > 0", 400)

        previous, currency = self.operation_value(op)
        value = round(float(amount), 2)
        if abs(value - previous) < 0.01:
            return op.dict()

        now = datetime.now(timezone.utc)
        op.amount = value
        if not op.currency:
            op.currency = currency
        equivalents = valuation.equivalents(self.db, value, op.currency, now)
        op.amount_usdt = equivalents["usdt_amount"]
        op.usdt_rate = equivalents["usdt_rate"]
        op.bcv_amount = equivalents["bcv_amount"]
        op.bcv_rate = equivalents["bcv_rate"]
        op.valuation_at = now

        # La cotización acompaña al valor con la misma tasa que se le prometió al cliente.
        if op.from_amount and op.to_amount:
            ratio = op.to_amount / op.from_amount
            op.from_amount = value
            op.to_amount = round(value * ratio, 2)

        released = self._trim_allocations_to_value(op, value)
        self._trim_settlements_to_value(op, value)
        self.db.flush()
        self._sync_status_from_delivery(op, actor)
        # El valor cambió: su transacción y las patas del fondo lo siguen.
        WhatsAppQuoteService(self.db)._sync_linked_transaction(op)
        self._sync_fund_legs(op, actor)
        self.db.commit()
        self.db.refresh(op)

        result = op.dict()
        result["released_from_allocations"] = released
        return result

    def _fund_leg_specs(self, op: WhatsAppOperation) -> list[tuple]:
        """
        Las patas que la operación declara: (fondo_id, tipo, monto, moneda).

        La pata que entra es lo que el cliente da; la que sale, lo que le pagamos. Cada una va
        en SU moneda, que por construcción es la del fondo que la lleva — por eso acá no hay
        ninguna conversión de moneda (la había `_fund_movement_figures`, que existía para
        expresar el valor de la op en la moneda de un fondo distinto).
        """
        cp = op.currency_pair
        if cp is None:
            return []
        from_symbol = cp.from_currency.symbol if cp.from_currency else None
        to_symbol = cp.to_currency.symbol if cp.to_currency else None

        specs = []
        if op.fund_group_id and op.from_amount and from_symbol:
            specs.append((
                op.fund_group_id,
                FundMovementType.EXCHANGE_IN,
                round(float(op.from_amount), 2),
                settlement_currency(from_symbol),
            ))
        if op.fund_group_out_id and op.to_amount and to_symbol:
            specs.append((
                op.fund_group_out_id,
                FundMovementType.EXCHANGE,
                round(float(op.to_amount), 2),
                settlement_currency(to_symbol),
            ))
        return specs

    def _sync_fund_legs(self, op: WhatsAppOperation, actor: Optional[User] = None) -> None:
        """
        Deja el libro igual a lo que dice la operación: hasta dos movimientos, uno por pata
        con fondo. Crea, actualiza y borra; correrla dos veces deja el mismo resultado.

        Reemplaza a `_sync_fund_movement` (que solo reajustaba) y a la creación suelta dentro
        de `create_operation_from_payment`: eran dos caminos que podían divergir.

        No usa `FundRepository.create_movement` a propósito: ese hace `commit()`, y esto corre
        dentro de operaciones de servicio que todavía no terminaron.
        """
        existing = {
            m.movement_type: m
            for m in self.db.query(FundMovement)
            .filter(FundMovement.transaction_id == op.transaction_id)
            .all()
        } if op.transaction_id is not None else {}

        # Solo una operación completada movió plata. Cancelarla o devolverla a cotizada
        # retira sus movimientos.
        specs = (
            self._fund_leg_specs(op)
            if op.status == WhatsAppOperationStatus.COMPLETED
            else []
        )

        if specs and op.transaction_id is None:
            # Un movimiento siempre cuelga de una transacción — es lo que lo hace
            # desaparecer por CASCADE si la op se borra. Sin `transaction_id`, `existing`
            # queda vacío (no hay con qué correlacionar), y sin este guard el bloque de
            # abajo crearía movimientos sueltos con `transaction_id=None`, duplicados en
            # cada corrida porque nunca se encontrarían a sí mismos la próxima vez.
            specs = []

        user_id = (actor.id if actor else None) or op.received_by_user_id
        if specs and user_id is None:
            # Sin gestor no se puede registrar, pero eso nunca tumba la operación.
            specs = []

        at = op.valuation_at or op.quoted_at or datetime.now(timezone.utc)
        wanted_types = set()
        for group_id, mtype, amount, currency in specs:
            wanted_types.add(mtype)
            eq = valuation.equivalents(self.db, amount, currency, at)
            usdt = eq["usdt_amount"]
            rate = (amount / usdt) if usdt else None

            movement = existing.get(mtype)
            if movement is None:
                movement = FundMovement(
                    movement_type=mtype,
                    transaction_id=op.transaction_id,
                    movement_date=at,
                    user_id=user_id,
                    recorded_by_user_id=actor.id if actor else None,
                )
                self.db.add(movement)
            movement.group_id = group_id
            movement.amount = amount
            movement.currency = currency
            movement.amount_usdt = usdt
            movement.usdt_rate = rate

        for mtype, movement in existing.items():
            if mtype in (FundMovementType.EXCHANGE, FundMovementType.EXCHANGE_IN) and mtype not in wanted_types:
                self.db.delete(movement)

        self.db.flush()

    def _resolve_fund_legs_for_new_op(self, op: WhatsAppOperation) -> None:
        """
        Completa los fondos que la operación no trae, por la moneda de cada pata.

        Corre UNA vez, al nacer la operación. Lo que el caller ya puso (el fondo elegido a
        mano, o el heredado del comprobante) no se pisa: solo se rellena lo que está en NULL.
        """
        cp = op.currency_pair
        if cp is None:
            return
        repo = FundRepository(self.db)
        if op.fund_group_id is None and cp.from_currency:
            group = repo.get_active_group_by_currency(
                settlement_currency(cp.from_currency.symbol)
            )
            op.fund_group_id = group.id if group else None
        if op.fund_group_out_id is None and cp.to_currency:
            group = repo.get_active_group_by_currency(
                settlement_currency(cp.to_currency.symbol)
            )
            op.fund_group_out_id = group.id if group else None

    def _trim_settlements_to_value(self, op: WhatsAppOperation, value: float) -> None:
        """
        Recorta cuánto cubren los salientes cuando el valor baja por debajo de lo entregado:
        si el trato ahora vale menos, un comprobante no puede seguir cubriendo de más. Se
        reduce a prorrata; la tasa efectiva de cada pago se recalcula sola (amount/settled).
        """
        payouts = (
            self.db.query(WhatsAppOutgoingPayment)
            .filter(
                WhatsAppOutgoingPayment.whatsapp_operation_id == op.id,
                WhatsAppOutgoingPayment.settled_amount.isnot(None),
            )
            .order_by(WhatsAppOutgoingPayment.id)
            .all()
        )
        covered = round(sum(p.settled_amount for p in payouts), 2)
        if not payouts or covered <= value + 0.01:
            return

        remaining = value
        for index, payout in enumerate(payouts):
            if index == len(payouts) - 1:
                payout.settled_amount = round(max(remaining, 0), 2)
            else:
                share = round(payout.settled_amount * value / covered, 2)
                payout.settled_amount = share
                remaining = round(remaining - share, 2)

    def _trim_allocations_to_value(self, op: WhatsAppOperation, value: float) -> float:
        """
        Recorta el reparto de los entrantes cuando el valor baja: se reduce a prorrata y se
        devuelve cuánto quedó liberado (sin asignar en sus comprobantes).
        """
        allocations = (
            self.db.query(WhatsAppPaymentAllocation)
            .filter(WhatsAppPaymentAllocation.whatsapp_operation_id == op.id)
            .order_by(WhatsAppPaymentAllocation.id)
            .all()
        )
        assigned = round(sum(a.amount for a in allocations), 2)
        if not allocations or assigned <= value + 0.01:
            return 0.0

        remaining = value
        for index, allocation in enumerate(allocations):
            if index == len(allocations) - 1:
                allocation.amount = round(max(remaining, 0), 2)
            else:
                share = round(allocation.amount * value / assigned, 2)
                allocation.amount = share
                remaining = round(remaining - share, 2)
        return round(assigned - value, 2)

    def set_personal_expense(
        self,
        payment_id: int,
        is_personal: bool,
        description: Optional[str],
        actor: Optional[User] = None,
        orphan_action: Optional[str] = None,
        orphan_note: Optional[str] = None,
    ) -> dict:
        row = self._get_or_404("outgoing", payment_id)
        if is_personal:
            self._assert_not_loan(payment_id)
        # Marcarlo como personal lo desvincula: si era el único comprobante de la op, pasa
        # por la misma decisión que un desvinculado explícito.
        orphaned_op = (
            self._resolve_orphan(row, "outgoing", payment_id, orphan_action, orphan_note, actor)
            if is_personal
            else None
        )
        row.is_personal_expense = is_personal
        if is_personal:
            row.personal_description = description
            row.whatsapp_operation_id = None  # un gasto personal no se vincula a op de cliente
        else:
            row.personal_description = None
        self.db.commit()
        self.db.refresh(row)
        if orphaned_op is not None:
            WhatsAppQuoteService(self.db).delete_operation(orphaned_op)
        return self._with_name(row)

    def set_irrelevant(
        self,
        payment_id: int,
        is_irrelevant: bool,
        description: Optional[str] = None,
        actor: Optional[User] = None,
        orphan_action: Optional[str] = None,
        orphan_note: Optional[str] = None,
    ) -> dict:
        row = self._get_or_404("outgoing", payment_id)
        if is_irrelevant:
            self._assert_not_loan(payment_id)
        orphaned_op = (
            self._resolve_orphan(row, "outgoing", payment_id, orphan_action, orphan_note, actor)
            if is_irrelevant
            else None
        )
        row.is_irrelevant = is_irrelevant
        if is_irrelevant:
            row.irrelevant_description = description
            row.whatsapp_operation_id = None
        else:
            row.irrelevant_description = None
        self.db.commit()
        self.db.refresh(row)
        if orphaned_op is not None:
            WhatsAppQuoteService(self.db).delete_operation(orphaned_op)
        return self._with_name(row)

    def unlink_preview(self, table: str, payment_id: int) -> dict:
        """
        Qué pasaría si este pago se desvincula de su operación: si la dejaría sin ningún
        comprobante y, en ese caso, todo lo que se borraría al elegir borrarla. Es lo que el
        front muestra en el cuadro de confirmación.
        """
        row = self._get_or_404(table, payment_id)
        op = row.operation
        if op is None:
            return {"would_orphan": False, "operation": None}
        if self._payments_left_without(op.id, table, payment_id) > 0:
            return {"would_orphan": False, "operation": op.dict()}

        quote_svc = WhatsAppQuoteService(self.db)
        movements = quote_svc.orphan_fund_movements(op)
        balance_entries = (
            self.db.query(WhatsAppBalanceEntry.id)
            .filter(WhatsAppBalanceEntry.whatsapp_operation_id == op.id)
            .count()
        )
        return {
            "would_orphan": True,
            "operation": op.dict(),
            "transaction_uuid": op.transaction.uuid if op.transaction else None,
            "fund_group_name": op.fund_group.name if op.fund_group else None,
            "fund_movements": [
                {
                    "uuid": m.uuid,
                    "movement_type": m.movement_type.value if m.movement_type else None,
                    "amount": m.amount,
                    "currency": m.currency,
                }
                for m in movements
            ],
            "balance_entries": balance_entries,
            # Con saldo de por medio el borrado no está disponible: solo mantener.
            "can_delete": balance_entries == 0,
        }

    def mark_incoming_forwarded_to_group(
        self,
        payment_id: int,
        group_jid: Optional[str] = None,
        group_uuid: Optional[UUID] = None,
    ) -> dict:
        """
        Marca un pago ENTRANTE como contabilizado en un grupo (FundGroup), al reenviarlo
        el operador al grupo (escenario ZELLE_DIRECT). Resuelve el grupo por su JID de
        WhatsApp (`whatsapp_group_jid`) o por uuid. No crea ningún saliente.

        El grupo se guarda en la OPERACIÓN del pago (el pago ya no tiene columna propia: el
        fondo se deriva de la op). Si el entrante todavía no está vinculado a una operación,
        no hay dónde anotarlo — el bot etiqueta la op un instante después con `set_scenario`,
        que vuelve a fijar el grupo.
        """
        row = self._get_or_404("incoming", payment_id)
        group = None
        if group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(group_uuid)).first()
        elif group_jid:
            group = self.db.query(FundGroup).filter(FundGroup.whatsapp_group_jid == group_jid).first()
        if group is None:
            raise QuoteServiceError(
                "fund_group_not_found", f"Fondo para grupo {group_uuid or group_jid} no encontrado", 404
            )
        op = row.operation
        if op is not None and op.fund_group_id != group.id:
            op.fund_group_id = group.id
            self.db.commit()
            self.db.refresh(row)
        return self._with_name(row)

    @staticmethod
    def _payment_copy_kwargs(payment) -> dict:
        """Campos comunes que deben sobrevivir al mover un comprobante entre bandejas."""
        return {
            "client_phone": payment.client_phone,
            "provider": payment.provider,
            "amount": payment.amount,
            "currency": payment.currency,
            "bank_from": payment.bank_from,
            "bank_to": payment.bank_to,
            "account_number": payment.account_number,
            "identification": payment.identification,
            "phone_to": payment.phone_to,
            "reference": payment.reference,
            "raw_text": payment.raw_text,
            "whatsapp_operation_id": payment.whatsapp_operation_id,
            "corrected_at": payment.corrected_at,
            "correction_original": payment.correction_original,
            "created_at": payment.created_at,
        }

    def convert_outgoing_to_incoming(
        self,
        payment_id: int,
        group_jid: Optional[str] = None,
        group_uuid: Optional[UUID] = None,
    ) -> dict:
        """
        Mueve un pago SALIENTE a ENTRANTE conservando comprobante, fecha y operación.
        El grupo es opcional: se usa cuando conocemos el fondo del reenvío, pero no se exige
        para corregir pagos de números no trackeados que llegaron al lado saliente.
        """
        out = self._get_or_404("outgoing", payment_id)
        self._assert_not_loan(payment_id)

        # Grupo: explícito (uuid), o por JID del grupo (parámetro o el client_phone si es @g.us).
        group = None
        if group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(group_uuid)).first()
        else:
            jid = group_jid or (out.client_phone if (out.client_phone or "").endswith("@g.us") else None)
            if jid:
                group = self.db.query(FundGroup).filter(FundGroup.whatsapp_group_jid == jid).first()
        if (group_uuid is not None or group_jid) and group is None:
            raise QuoteServiceError(
                "fund_group_not_found", "No se pudo resolver el fondo del grupo", 404
            )

        incoming = WhatsAppIncomingPayment(**self._payment_copy_kwargs(out))
        op = out.operation
        # El fondo va en la operación (el pago lo deriva). Si el pago aún no tiene op, el
        # grupo se sigue viendo por el JID en client_phone.
        if group is not None and op is not None and op.fund_group_id is None:
            op.fund_group_id = group.id
        self.db.add(incoming)
        self.db.delete(out)
        self.db.commit()
        self.db.refresh(incoming)
        return self._with_name(incoming)

    # Nombre anterior conservado para el endpoint del bot y clientes antiguos.
    def convert_outgoing_to_group_incoming(
        self,
        payment_id: int,
        group_jid: Optional[str] = None,
        group_uuid: Optional[UUID] = None,
    ) -> dict:
        return self.convert_outgoing_to_incoming(payment_id, group_jid, group_uuid)

    def convert_incoming_to_outgoing(self, payment_id: int) -> dict:
        """Revierte un entrante todavía no contabilizado y lo devuelve a salientes."""
        incoming = self._get_or_404("incoming", payment_id)

        deposit = self.db.query(FundMovement.id).filter(FundMovement.incoming_payment_id == payment_id).first()
        if deposit is not None:
            raise QuoteServiceError(
                "incoming_has_deposit",
                "Este pago ya generó un depósito a un fondo y no puede convertirse en saliente",
                409,
            )

        balance_entry = (
            self.db.query(WhatsAppBalanceEntry.id)
            .filter(WhatsAppBalanceEntry.incoming_payment_id == payment_id)
            .first()
        )
        if balance_entry is not None:
            raise QuoteServiceError(
                "incoming_has_balance_credit",
                "Este pago ya fue acreditado como saldo y no puede convertirse en saliente",
                409,
            )

        forwarded = (
            self.db.query(WhatsAppOutgoingPayment.id)
            .filter(WhatsAppOutgoingPayment.source_payment_id == payment_id)
            .first()
        )
        if forwarded is not None:
            raise QuoteServiceError(
                "incoming_is_payment_source",
                "Este pago está enlazado a otro comprobante saliente y no puede convertirse",
                409,
            )

        outgoing = WhatsAppOutgoingPayment(**self._payment_copy_kwargs(incoming))
        self.db.add(outgoing)
        self.db.delete(incoming)
        self.db.commit()
        self.db.refresh(outgoing)
        return self._with_name(outgoing)

    # Un pago entrante NO se puede convertir en depósito: el dinero del cliente entra al fondo
    # como pata USD del cambio (FundMovement EXCHANGE vía la transacción de la operación).
    # El único camino a un DEPOSIT es `fund_pending_deposits` — comprobante de reposición del
    # gestor en el grupo, o alta manual en /admin/funds. `_attach_deposits` se queda porque
    # sigue mostrando los depósitos históricos ligados a un entrante (0 filas en prod).

    # ---------- Crear operación desde pago ----------

    def _resolve_operation_fund_group(
        self,
        row,
        fund_group_uuid: Optional[UUID],
        fund_group_provided: bool,
    ):
        """
        Fondo de la op que nace de un comprobante. El explícito manda; si el caller omitió
        el campo, se hereda el del pago (ej. reenviado al grupo y convertido a entrante),
        que si no quedaría huérfano. Un null explícito significa "sin fondo" y se respeta,
        para no pisar al operador que lo quitó a propósito.
        """
        if fund_group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(fund_group_uuid)).first()
            if group is None:
                raise QuoteServiceError("fund_group_not_found", f"Fondo {fund_group_uuid} no encontrado", 404)
            return group
        # Heredado del pago: hoy el entrante lo deriva del JID del grupo en `client_phone`
        # (ya no hay columna propia); el saliente nunca tuvo fondo.
        if not fund_group_provided:
            return getattr(row, "fund_group", None)
        return None

    def _resolve_operation_client(self, quote_svc: WhatsAppQuoteService, row, group):
        """
        Cliente de la op que nace de un comprobante. Un grupo contable NO es un cliente: si
        el comprobante llegó reenviado al grupo, al cliente real lo atendió el operador por
        fuera del bot y todavía no sabemos quién es. La op nace con el cliente anónimo del
        grupo y se resuelve al vincular el saliente (`set_operation`) o desde su detalle.
        """
        if (row.client_phone or "").endswith("@g.us"):
            source_group = (
                self.db.query(FundGroup)
                .filter(FundGroup.whatsapp_group_jid == row.client_phone)
                .first()
            ) or group
            if source_group is not None:
                return quote_svc.upsert_anonymous_group_client(source_group)
        return quote_svc.upsert_client(row.client_phone)

    def create_operation_from_payment(
        self, table: str, payment_id: int, from_currency: str, to_currency: str,
        from_amount: float, to_amount: float,
        amount_side: str = "SEND",
        fund_group_uuid: Optional[UUID] = None,
        exchange_user_uuid: Optional[UUID] = None,
        recorded_by_user_id: Optional[int] = None,
        fund_group_provided: bool = False,
    ) -> dict:
        row = self._get_or_404(table, payment_id)
        if table == "outgoing":
            self._assert_not_loan(payment_id)
        if from_amount <= 0 or to_amount <= 0:
            raise QuoteServiceError("invalid_amount", "Montos deben ser > 0", 400)

        try:
            side = WhatsAppAmountSide(amount_side)
        except ValueError:
            raise QuoteServiceError("invalid_amount_side", f"amount_side inválido: {amount_side}", 400)

        pair_symbol = f"{from_currency}-{to_currency}"
        pair = self.pair_repo.get_by_symbol(pair_symbol)
        if pair is None:
            pair = self.pair_repo.get_by_symbol(f"{to_currency}-{from_currency}")
        if pair is None:
            raise QuoteServiceError("pair_not_found", f"No existe currency pair para {from_currency}/{to_currency}", 404)

        # Fondo opcional (etiqueta + movimiento EXCHANGE). Resolver antes de crear la op.
        group = self._resolve_operation_fund_group(row, fund_group_uuid, fund_group_provided)

        quote_svc = WhatsAppQuoteService(self.db)
        client = self._resolve_operation_client(quote_svc, row, group)

        now = datetime.utcnow()
        track_delivery = table == "outgoing" and from_currency == "USD"
        # El valor del trato es lo que entrega el cliente; el par y el `to` son la cotización.
        value = valuation.equivalents(self.db, from_amount, from_currency, now)
        # Una op que nace de un comprobante no trae margen cotizado: sin esto la operación
        # queda sin ganancia (la transacción la calcula desde `applied_percentage`). Se lee de
        # la tasa que se terminó usando contra la base del par, así vale igual si el operador
        # cotizó a la tasa del día o a una propia.
        rate_used = to_amount / from_amount
        entry = WhatsAppRateResolver(self.db).get_rate_entry_for_pair(from_currency, to_currency)
        applied_percentage = WhatsAppRateResolver.implied_margin(entry, rate_used)
        op = WhatsAppOperation(
            client_id=client.id,
            currency_pair_id=pair.id,
            amount=from_amount,
            currency=from_currency,
            amount_usdt=value["usdt_amount"],
            usdt_rate=value["usdt_rate"],
            bcv_amount=value["bcv_amount"],
            bcv_rate=value["bcv_rate"],
            valuation_at=now,
            from_amount=from_amount,
            to_amount=to_amount,
            rate_used=rate_used,
            inverse_percentage=False,
            applied_percentage=applied_percentage,
            default_percentage=entry.base_percentage if entry else None,
            amount_side=side,
            status=WhatsAppOperationStatus.PENDING,
            delivery_status=WhatsAppDeliveryStatus.PENDING if track_delivery else None,
            fund_group_id=group.id if group else None,
            quoted_at=now,
            expires_at=now + timedelta(minutes=QUOTE_TTL_MINUTES),
            approved_at=now,
        )
        self.db.add(op)
        self.db.flush()
        # La op nace con los dos fondos resueltos por moneda; lo que ya vino puesto (el
        # entrante explícito o el heredado del comprobante) no se toca.
        self._resolve_fund_legs_for_new_op(op)
        self.db.flush()
        row.whatsapp_operation_id = op.id
        # El entrante estrena su parte del reparto: lo que pide la op, y si el comprobante
        # trae más, la diferencia queda visible como "sin asignar".
        if table == "incoming":
            self._upsert_allocation(row, op)
        else:
            # Crear la operación DESDE un comprobante de salida afirma que ese pago es el que
            # la cubre: el valor se fijó mirándolo. Si el operador puso un valor distinto al
            # que da la tasa, la tasa efectiva del pago sale de ahí.
            self._apply_settlement(row, op, from_amount)

        # Con fondo en cualquiera de las dos patas hace falta la transacción, para que los
        # movimientos cuelguen de ella y se vayan con ella (FK ON DELETE CASCADE) si la op
        # se borra. El movimiento en sí lo deja `_sync_fund_legs` al final de la función, una
        # vez que se conoce el estado final de la operación (PENDING o COMPLETED, según el
        # bloque de abajo) — antes se creaba acá mismo vía `_fund_movement_figures`, que
        # convertía de moneda; ahora cada pata va en la suya y el helper decide si corresponde.
        transaction_user = None
        if op.fund_group_id or op.fund_group_out_id:
            exchange_user_id = recorded_by_user_id
            if exchange_user_uuid is not None:
                user = self.db.query(User).filter(User.uuid == exchange_user_uuid).first()
                if user is None:
                    raise QuoteServiceError("user_not_found", f"Usuario {exchange_user_uuid} no encontrado", 404)
                exchange_user_id = user.id

            transaction_user = (
                self.db.query(User).filter(User.id == exchange_user_id).first()
                if exchange_user_id
                else None
            )
            # Sin gestor no hay quién firme la transacción, pero eso nunca tumba la
            # operación: `_sync_fund_legs` (regla 6) simplemente no registra las patas.
            if transaction_user is not None:
                # La transacción va primero para que el movimiento cuelgue de ella: así el
                # movimiento se va con la transacción (FK ON DELETE CASCADE) si la op se borra.
                tx = quote_svc._create_transaction_for_op(
                    op,
                    WhatsAppOperationComplete(),
                    transaction_user,
                )
                op.transaction_id = tx.id

        # Un entrante inicia la operación pero no confirma que el dinero haya sido entregado al
        # cliente: siempre permanece PENDING hasta vincular el pago saliente. Si la operación se
        # crea desde el propio saliente, sí puede completarse de inmediato, salvo la entrega física
        # de USD.
        if table == "outgoing" and from_currency.upper() != "USD":
            completing_user = (
                self.db.query(User).filter(User.id == recorded_by_user_id).first()
                if recorded_by_user_id
                else None
            )
            if completing_user is None:
                raise QuoteServiceError(
                    "complete_user_required", "Falta el usuario que completa la operación", 400
                )
            op.status = WhatsAppOperationStatus.COMPLETED
            op.completed_at = now
            tx = quote_svc._create_transaction_for_op(op, WhatsAppOperationComplete(), completing_user)
            op.transaction_id = tx.id
            transaction_user = transaction_user or completing_user

        # El libro sigue a la operación: si terminó COMPLETED, deja hasta dos movimientos.
        self._sync_fund_legs(op, transaction_user)

        self.db.commit()
        self.db.refresh(op)
        return op.dict()

    # ---------- Correcciones ----------

    def list_corrected(self) -> list[dict]:
        out = []
        for table, Model in (("incoming_payments", WhatsAppIncomingPayment), ("outgoing_payments", WhatsAppOutgoingPayment)):
            rows = (
                self.db.query(Model)
                .filter(Model.corrected_at.isnot(None))
                .order_by(Model.corrected_at.desc())
                .all()
            )
            for row in rows:
                original = json.loads(row.correction_original) if row.correction_original else {}
                corrected = {k: getattr(row, k, None) for k in original.keys()}
                out.append({
                    "table": table,
                    "id": row.id,
                    "client_phone": row.client_phone,
                    "created_at": row.created_at,
                    "corrected_at": row.corrected_at,
                    "original": original,
                    "corrected": corrected,
                })
        return out
