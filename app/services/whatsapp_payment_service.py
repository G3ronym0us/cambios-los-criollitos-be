"""
Servicio de pagos de WhatsApp (comprobantes OCR). Espejo de las funciones de
`whatsapp-bot/src/operations.ts` (save/list/update/link/personal/irrelevant/
create-op-from-payment/corrected).

El matching difuso (findOperationByOutgoingPayment / findIncomingForwardedToGroup)
se queda en el bot; aquí solo persistimos y resolvemos vínculos.
"""

import json
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import exists, or_
from sqlalchemy.orm import Session, joinedload

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
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.models.client_loan import ClientLoan
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.fund_repository import FundRepository
from app.schemas.whatsapp import WhatsAppOperationComplete
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService


EDITABLE_FIELDS = ["provider", "amount", "currency", "bank_from", "bank_to", "identification", "phone_to", "reference"]
QUOTE_TTL_MINUTES = 30

# ZELLE y PAYPAL son métodos de pago en dólares, no monedas propias: para la contabilidad de
# fondos se liquidan como USD (un fondo en USD debe matchear un lado ZELLE/PAYPAL de la op).
_SETTLEMENT_CURRENCY = {"ZELLE": "USD", "PAYPAL": "USD"}


def settlement_currency(symbol: Optional[str]) -> str:
    up = (symbol or "").upper()
    return _SETTLEMENT_CURRENCY.get(up, up)


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
        return self._with_name(row)

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

    def list_payments_page(
        self,
        table: str,
        limit: int = 50,
        offset: int = 0,
        search: Optional[str] = None,
        out_class: str = "ALL",
        unlinked_only: bool = False,
    ) -> dict:
        """Página de pagos para el front: búsqueda + clasificación server-side. Devuelve {items, total}."""
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

        total = q.count()
        rows = q.order_by(Model.created_at.desc()).limit(limit).offset(offset).all()
        items = [self._row_to_dict(*r) for r in rows]
        if table == "incoming" and items:
            self._attach_deposits(items)
        if table == "outgoing" and items:
            self._attach_loans(items)
        return {"items": items, "total": total}

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

    def list_payments_for_operation(self, op_uuid: UUID) -> dict:
        """Pagos entrantes + salientes vinculados a una operación (para el detalle de la op)."""
        op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_uuid).first()
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operation {op_uuid} no encontrada", 404)
        inc_rows = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.whatsapp_operation_id == op.id)
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

    def set_operation(
        self,
        table: str,
        payment_id: int,
        operation_uuid: Optional[UUID],
        completing_user: Optional[User] = None,
        complete_outgoing: bool = False,
        settle_amount: Optional[float] = None,
    ) -> dict:
        row = self._get_or_404(table, payment_id)
        if table == "outgoing" and operation_uuid is not None:
            self._assert_not_loan(payment_id)
        op = None
        if operation_uuid is not None:
            op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == operation_uuid).first()
            if op is None:
                raise QuoteServiceError("op_not_found", f"Operation {operation_uuid} no encontrada", 404)
            Model = self._model(table)
            same_side_payment = (
                self.db.query(Model.id)
                .filter(
                    Model.whatsapp_operation_id == op.id,
                    Model.id != row.id,
                )
                .first()
            )
            if same_side_payment is not None:
                side_label = "entrante" if table == "incoming" else "saliente"
                raise QuoteServiceError(
                    "operation_payment_already_linked",
                    f"La operación ya tiene un pago {side_label} vinculado",
                    409,
                )
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
            row.whatsapp_operation_id = op.id
            # Sincroniza client_phone al de la op (el operador afirma "este pago es de este cliente").
            if op.client and op.client.phone:
                row.client_phone = op.client.phone
        else:
            row.whatsapp_operation_id = None

        # Liquidación parcial: el cliente envió el total de la op (ej. 200) pero solo pidió
        # cambiar una parte (ej. 30). Se redimensiona la op al monto realmente cambiado
        # (mismo ratio from→to de la cotización) y el excedente se ACREDITA como saldo a
        # favor ANTES de completar, para que la transacción contable nazca con los montos
        # reales. Solo aplica al vincular el saliente que completa la op.
        if settle_amount is not None:
            self._apply_partial_settle(table, op, settle_amount, complete_outgoing, completing_user)

        # En el panel, vincular manualmente una captura saliente confirma que el
        # dinero fue enviado. La operación y su transacción deben completarse en
        # la misma acción. El endpoint del bot no activa esta rama porque ese
        # flujo ya llama explícitamente a complete_operation después del match.
        if (
            complete_outgoing
            and table == "outgoing"
            and op is not None
            and op.status in (WhatsAppOperationStatus.QUOTED, WhatsAppOperationStatus.PENDING)
        ):
            if completing_user is None:
                raise QuoteServiceError(
                    "complete_user_required",
                    "Falta el usuario que completa la operación",
                    400,
                )
            self.db.flush()
            WhatsAppQuoteService(self.db).update_status(
                op.uuid,
                WhatsAppOperationStatus.COMPLETED.value,
                completing_user,
            )
        else:
            self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

    def _apply_partial_settle(
        self,
        table: str,
        op: Optional[WhatsAppOperation],
        settle_amount: float,
        complete_outgoing: bool,
        completing_user: Optional[User],
    ) -> None:
        """
        Redimensiona la op al monto USD realmente cambiado y acredita el excedente
        como saldo a favor (ledger `whatsapp_balance_entries`). NO comitea: el
        caller completa la op (y comitea) en la misma acción.
        """
        if not (complete_outgoing and table == "outgoing" and op is not None):
            raise QuoteServiceError(
                "partial_settle_invalid",
                "El monto parcial solo aplica al vincular un saliente a una operación",
                400,
            )
        if op.status not in (WhatsAppOperationStatus.QUOTED, WhatsAppOperationStatus.PENDING):
            raise QuoteServiceError(
                "partial_settle_invalid_status",
                f"La operación está {op.status.value}; solo se liquida parcial una op activa",
                409,
            )
        self._partial_settle_core(op, settle_amount, completing_user)

    def partial_settle_completed(
        self,
        op_uuid: UUID,
        settle_amount: float,
        completing_user: Optional[User],
    ) -> dict:
        """
        Corrección retroactiva: una op ya COMPLETED que se completó por el total
        cuando el cliente solo cambió una parte. Redimensiona la op, sincroniza la
        transacción contable (montos + ganancia + splits) y acredita el excedente
        como saldo a favor.
        """
        op = (
            self.db.query(WhatsAppOperation)
            .filter(WhatsAppOperation.uuid == op_uuid)
            .first()
        )
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operation {op_uuid} no encontrada", 404)
        if op.status != WhatsAppOperationStatus.COMPLETED:
            raise QuoteServiceError(
                "partial_settle_invalid_status",
                f"La operación está {op.status.value}; la corrección aplica a ops COMPLETED "
                "(para una op activa usa el monto parcial al vincular el saliente)",
                409,
            )

        surplus = self._partial_settle_core(op, settle_amount, completing_user)
        # Espejo contable: la transacción (y sus profit splits) deben reflejar los
        # montos corregidos, no los originales.
        WhatsAppQuoteService(self.db)._sync_linked_transaction(op)
        self.db.commit()
        self.db.refresh(op)

        from app.services.whatsapp_balance_service import WhatsAppBalanceService

        return {
            "operation": op.dict(),
            "credited": surplus,
            "balance_after": WhatsAppBalanceService(self.db).get_balance(op.client_id),
        }

    def _partial_settle_core(
        self,
        op: WhatsAppOperation,
        settle_amount: float,
        completing_user: Optional[User],
    ) -> float:
        """
        Validaciones + redimensión + crédito del excedente, compartido entre la
        liquidación parcial al vincular y la corrección retroactiva. NO comitea.
        Devuelve el excedente acreditado.
        """
        cp = op.currency_pair
        from_symbol = cp.from_currency.symbol if cp and cp.from_currency else None
        if settlement_currency(from_symbol) != "USD":
            raise QuoteServiceError(
                "partial_settle_unsupported",
                f"Solo ops con lado origen USD/ZELLE/PAYPAL acreditan excedente (origen: {from_symbol})",
                400,
            )
        if op.client_id is None:
            raise QuoteServiceError(
                "partial_settle_no_client",
                "La operación no tiene cliente al que acreditar el excedente",
                400,
            )
        if settle_amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto cambiado debe ser > 0", 400)

        # Una sola corrección por op: si ya existe un crédito de excedente para esta
        # operación, un segundo achicaría los montos otra vez y duplicaría saldo.
        prior_credit = (
            self.db.query(WhatsAppBalanceEntry)
            .filter(
                WhatsAppBalanceEntry.whatsapp_operation_id == op.id,
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
            )
            .first()
        )
        if prior_credit is not None:
            raise QuoteServiceError(
                "partial_settle_already_applied",
                f"Esta operación ya acreditó un excedente de {prior_credit.amount:.2f} USD",
                409,
            )

        surplus = round(op.from_amount - settle_amount, 2)
        if surplus <= 0.01:
            raise QuoteServiceError(
                "partial_settle_not_partial",
                f"El monto cambiado ({settle_amount:.2f}) debe ser menor al total de la op ({op.from_amount:.2f})",
                400,
            )

        original_from = op.from_amount
        ratio = op.to_amount / op.from_amount
        op.from_amount = round(settle_amount, 2)
        op.to_amount = round(settle_amount * ratio, 2)

        # Trazabilidad: si la op tiene un entrante vinculado que aún no fue acreditado,
        # el crédito lo referencia (misma restricción de un-crédito-por-entrante que
        # usa credit_from_incoming).
        incoming_id = None
        incoming = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.whatsapp_operation_id == op.id)
            .first()
        )
        if incoming is not None:
            already = (
                self.db.query(WhatsAppBalanceEntry)
                .filter(
                    WhatsAppBalanceEntry.incoming_payment_id == incoming.id,
                    WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
                )
                .first()
            )
            if already is None:
                incoming_id = incoming.id

        self.db.add(
            WhatsAppBalanceEntry(
                client_id=op.client_id,
                entry_type=WhatsAppBalanceEntryType.CREDIT,
                amount=surplus,
                currency="USD",
                incoming_payment_id=incoming_id,
                whatsapp_operation_id=op.id,
                notes=(
                    f"Excedente de op {str(op.uuid)[:8]}: cambió {settle_amount:.2f} "
                    f"de {original_from:.2f} {from_symbol}"
                ),
                created_by_user_id=completing_user.id if completing_user else None,
            )
        )
        return surplus

    def set_personal_expense(self, payment_id: int, is_personal: bool, description: Optional[str]) -> dict:
        row = self._get_or_404("outgoing", payment_id)
        if is_personal:
            self._assert_not_loan(payment_id)
        row.is_personal_expense = is_personal
        if is_personal:
            row.personal_description = description
            row.whatsapp_operation_id = None  # un gasto personal no se vincula a op de cliente
        else:
            row.personal_description = None
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

    def set_irrelevant(self, payment_id: int, is_irrelevant: bool, description: Optional[str] = None) -> dict:
        row = self._get_or_404("outgoing", payment_id)
        if is_irrelevant:
            self._assert_not_loan(payment_id)
        row.is_irrelevant = is_irrelevant
        if is_irrelevant:
            row.irrelevant_description = description
            row.whatsapp_operation_id = None
        else:
            row.irrelevant_description = None
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

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
        row.fund_group_id = group.id
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

        incoming = WhatsAppIncomingPayment(
            **self._payment_copy_kwargs(out),
            fund_group_id=group.id if group else None,
        )
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

    # ---------- Depósito a fondo desde pago entrante ----------

    def create_deposit_from_payment(
        self,
        payment_id: int,
        group_uuid: UUID,
        user_uuid: UUID,
        amount: float,
        currency: str,
        deposit_method: str,
        reference: Optional[str] = None,
        notes: Optional[str] = None,
        recorded_by_user_id: Optional[int] = None,
    ) -> dict:
        """Registra un pago ENTRANTE como depósito (FundMovement DEPOSIT) a un fondo."""
        row = self._get_or_404("incoming", payment_id)

        group = self.db.query(FundGroup).filter(FundGroup.uuid == str(group_uuid)).first()
        if group is None:
            raise QuoteServiceError("fund_group_not_found", f"Fondo {group_uuid} no encontrado", 404)

        user = self.db.query(User).filter(User.uuid == user_uuid).first()
        if user is None:
            raise QuoteServiceError("user_not_found", f"Usuario {user_uuid} no encontrado", 404)

        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto debe ser > 0", 400)

        FundRepository(self.db).create_movement(
            group_id=group.id,
            user_id=user.id,
            movement_type=FundMovementType.DEPOSIT,
            amount=amount,
            currency=currency,
            movement_date=datetime.utcnow(),
            reference=reference,
            notes=notes,
            recorded_by_user_id=recorded_by_user_id,
            deposit_method=deposit_method,
            incoming_payment_id=row.id,
        )

        # Devolver el pago con el bloque `deposit` ya adjunto.
        d = self._with_name(row)
        self._attach_deposits([d])
        return d

    # ---------- Crear operación desde pago ----------

    def create_operation_from_payment(
        self, table: str, payment_id: int, from_currency: str, to_currency: str,
        from_amount: float, to_amount: float,
        amount_side: str = "SEND",
        fund_group_uuid: Optional[UUID] = None,
        exchange_user_uuid: Optional[UUID] = None,
        recorded_by_user_id: Optional[int] = None,
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
        group = None
        if fund_group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(fund_group_uuid)).first()
            if group is None:
                raise QuoteServiceError("fund_group_not_found", f"Fondo {fund_group_uuid} no encontrado", 404)

        quote_svc = WhatsAppQuoteService(self.db)
        client = quote_svc.upsert_client(row.client_phone)

        now = datetime.utcnow()
        track_delivery = table == "outgoing" and from_currency == "USD"
        op = WhatsAppOperation(
            client_id=client.id,
            currency_pair_id=pair.id,
            from_amount=from_amount,
            to_amount=to_amount,
            rate_used=to_amount / from_amount,
            inverse_percentage=False,
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
        row.whatsapp_operation_id = op.id

        # Salida con fondo: registrar el EXCHANGE (sale plata del fondo) por el lado de la op
        # cuya moneda coincide con la moneda base del fondo.
        if group is not None:
            exchange_user_id = recorded_by_user_id
            if exchange_user_uuid is not None:
                user = self.db.query(User).filter(User.uuid == exchange_user_uuid).first()
                if user is None:
                    raise QuoteServiceError("user_not_found", f"Usuario {exchange_user_uuid} no encontrado", 404)
                exchange_user_id = user.id
            if exchange_user_id is None:
                raise QuoteServiceError("exchange_user_required", "Falta el gestor del movimiento EXCHANGE", 400)

            # Match por moneda de liquidación: ZELLE/PAYPAL cuentan como USD. El movimiento se
            # registra en la moneda base del fondo (ej. un Zelle en un fondo USD → movimiento USD).
            base = settlement_currency(group.currency)
            if base == settlement_currency(from_currency):
                mv_amount = from_amount
            elif base == settlement_currency(to_currency):
                mv_amount = to_amount
            else:
                raise QuoteServiceError(
                    "fund_currency_mismatch",
                    f"El fondo está en {group.currency} y la operación es {from_currency}/{to_currency}",
                    400,
                )

            FundRepository(self.db).create_movement(
                group_id=group.id,
                user_id=exchange_user_id,
                movement_type=FundMovementType.EXCHANGE,
                amount=mv_amount,
                currency=base,
                movement_date=now,
                recorded_by_user_id=recorded_by_user_id,
            )

            transaction_user = self.db.query(User).filter(User.id == exchange_user_id).first()
            if transaction_user is None:
                raise QuoteServiceError("transaction_user_required", "Falta el usuario de la transacción", 400)
            tx = quote_svc._create_transaction_for_op(
                op,
                WhatsAppOperationComplete(),
                transaction_user,
            )
            op.transaction_id = tx.id

        # Un entrante inicia la operación pero no confirma que el dinero haya sido entregado al
        # cliente: siempre permanece PENDING hasta vincular el pago saliente. Si la operación se
        # crea desde el propio saliente, sí puede completarse de inmediato, salvo la entrega física
        # de USD. El movimiento EXCHANGE del fondo y la Transaction no duplican el fondo.
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
