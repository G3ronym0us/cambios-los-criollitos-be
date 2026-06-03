"""
Servicio orquestador del ciclo de vida de una operación de WhatsApp.

Flujo:
  create_quote   -> WhatsAppOperation(status=QUOTED, expires_at=now+30min)
  approve_quote  -> QUOTED -> PENDING
  cancel_op      -> ... -> CANCELLED
  complete_op    -> PENDING -> COMPLETED + crea Transaction (con profit splits si aplica)

El servicio nunca habla con HTTP; recibe Sessions y devuelve modelos.
La capa router lo expone vía /whatsapp/...
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.currency_pair import CurrencyPair
from app.models.fund import FundGroup, FundGroupMember
from app.models.transaction import Transaction, TransactionStatus
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationScenario,
    WhatsAppOperationStatus,
)
from app.repositories.commission_config_repository import CommissionConfigRepository
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.transaction_repository import TransactionRepository
from app.schemas.transaction import ProfitSplitCreate, TransactionCreate
from app.schemas.whatsapp import (
    WhatsAppOperationApprove,
    WhatsAppOperationCancel,
    WhatsAppOperationComplete,
    WhatsAppOperationCreate,
    WhatsAppOperationScenarioUpdate,
)
from app.services.bcv_service import get_cached_bcv_rate
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


QUOTE_TTL_MINUTES = 30


class QuoteServiceError(Exception):
    """Error de negocio del servicio. El router lo mapea a HTTPException."""

    def __init__(self, code: str, message: str, http_status: int = 400):
        self.code = code
        self.message = message
        self.http_status = http_status
        super().__init__(message)


class WhatsAppQuoteService:
    def __init__(self, db: Session):
        self.db = db
        self.resolver = WhatsAppRateResolver(db)
        self.pair_repo = CurrencyPairRepository(db)

    # ---------- Cliente ----------

    def upsert_client(self, phone: str, display_name: Optional[str] = None) -> WhatsAppClient:
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
        now = datetime.now(timezone.utc)
        if client is None:
            client = WhatsAppClient(phone=phone, display_name=display_name, last_seen_at=now)
            self.db.add(client)
            self.db.flush()
            return client

        if display_name and client.display_name != display_name:
            client.display_name = display_name
        client.last_seen_at = now
        return client

    # ---------- Crear cotización ----------

    def create_quote(self, payload: WhatsAppOperationCreate) -> WhatsAppOperation:
        client = self.upsert_client(payload.client_phone, payload.client_display_name)

        if client.is_blocked:
            raise QuoteServiceError("client_blocked", f"Cliente {payload.client_phone} está bloqueado", 403)

        # Resolver currency_pair_id: buscamos un pair "FROM-TO" registrado en el sistema.
        pair_symbol = f"{payload.from_currency}-{payload.to_currency}"
        currency_pair = self.pair_repo.get_by_symbol(pair_symbol)
        if currency_pair is None:
            # Buscamos el inverso; si existe lo usamos pero registramos la op con el par inverso
            inverse_symbol = f"{payload.to_currency}-{payload.from_currency}"
            currency_pair = self.pair_repo.get_by_symbol(inverse_symbol)
        if currency_pair is None:
            raise QuoteServiceError(
                "pair_not_found",
                f"No existe currency pair para {payload.from_currency}/{payload.to_currency}",
                404,
            )

        # Path BCV: USDT no autorizado + par involucra USDT/VES → forzar tasa BCV
        bcv_usd: Optional[float] = None
        if not client.is_usdt_authorized and "USDT" in (payload.from_currency, payload.to_currency) and "VES" in (
            payload.from_currency,
            payload.to_currency,
        ):
            bcv_usd = get_cached_bcv_rate(self.db)

        entry = self.resolver.get_rate_entry_for_pair(payload.from_currency, payload.to_currency)
        if entry is None and bcv_usd is None:
            raise QuoteServiceError(
                "rate_not_available",
                f"No hay tasa disponible para {payload.from_currency}/{payload.to_currency}",
                422,
            )

        # Cuando entramos por BCV path, sobreescribimos rate/inverse_percentage:
        # bcv_usd es VES/USD; si pedido es USDT->VES usamos directo; si VES->USDT, inverso.
        if bcv_usd is not None and entry is None:
            if payload.from_currency == "USDT" and payload.to_currency == "VES":
                rate = bcv_usd
                inverse_percentage = False
                base_rate = bcv_usd
                base_percentage = None
            else:
                rate = bcv_usd
                inverse_percentage = True
                base_rate = bcv_usd
                base_percentage = None
            applied_percentage = None
            default_percentage = None
        else:
            rate = entry.rate
            inverse_percentage = entry.inverse_percentage
            base_rate = entry.base_rate
            base_percentage = entry.base_percentage
            applied_percentage = entry.base_percentage
            default_percentage = entry.base_percentage

            # Margin override (solo si tenemos base_percentage para anchor)
            if payload.margin_override is not None and base_percentage is not None:
                override_rate = self.resolver.rate_with_margin(base_rate, payload.margin_override, inverse_percentage)
                if override_rate is not None:
                    rate = override_rate
                    applied_percentage = payload.margin_override

        # Calcular amounts según side
        if payload.amount_side == "SEND":
            from_amount = payload.amount
            to_amount = self.resolver.apply_rate(payload.amount, rate, inverse_percentage)
            side_enum = WhatsAppAmountSide.SEND
        else:
            to_amount = payload.amount
            from_amount = self.resolver.apply_rate(payload.amount, rate, not inverse_percentage)
            side_enum = WhatsAppAmountSide.RECEIVE

        # Cancelar cualquier cotización previa QUOTED del mismo cliente
        self._cancel_previous_quoted(client.id)

        now = datetime.now(timezone.utc)
        op = WhatsAppOperation(
            client_id=client.id,
            currency_pair_id=currency_pair.id,
            from_amount=from_amount,
            to_amount=to_amount,
            rate_used=rate,
            inverse_percentage=inverse_percentage,
            applied_percentage=applied_percentage,
            default_percentage=default_percentage,
            amount_side=side_enum,
            bcv_usd=bcv_usd,
            status=WhatsAppOperationStatus.QUOTED,
            notes=payload.notes,
            quoted_at=now,
            expires_at=now + timedelta(minutes=QUOTE_TTL_MINUTES),
        )
        self.db.add(op)
        self.db.commit()
        self.db.refresh(op)
        return op

    def _cancel_previous_quoted(self, client_id: int) -> None:
        previous = (
            self.db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.client_id == client_id,
                WhatsAppOperation.status == WhatsAppOperationStatus.QUOTED,
            )
            .all()
        )
        now = datetime.now(timezone.utc)
        for op in previous:
            op.status = WhatsAppOperationStatus.CANCELLED
            op.cancelled_at = now

    # ---------- Aprobar / Cancelar ----------

    def approve_quote(self, op_uuid: UUID, payload: WhatsAppOperationApprove) -> WhatsAppOperation:
        op = self._get_op_or_404(op_uuid)
        if op.status != WhatsAppOperationStatus.QUOTED:
            raise QuoteServiceError(
                "invalid_status",
                f"Solo se puede aprobar una op en QUOTED; estado actual: {op.status.value}",
                409,
            )
        if op.expires_at <= datetime.now(timezone.utc):
            raise QuoteServiceError("quote_expired", "La cotización expiró", 409)

        op.status = WhatsAppOperationStatus.PENDING
        op.approved_at = datetime.now(timezone.utc)
        if payload.notes:
            op.notes = (op.notes + "\n" if op.notes else "") + payload.notes
        self.db.commit()
        self.db.refresh(op)
        return op

    def cancel_operation(self, op_uuid: UUID, payload: WhatsAppOperationCancel) -> WhatsAppOperation:
        op = self._get_op_or_404(op_uuid)
        if op.status in (WhatsAppOperationStatus.COMPLETED, WhatsAppOperationStatus.CANCELLED):
            raise QuoteServiceError(
                "invalid_status",
                f"No se puede cancelar una op en {op.status.value}",
                409,
            )
        op.status = WhatsAppOperationStatus.CANCELLED
        op.cancelled_at = datetime.now(timezone.utc)
        if payload.reason:
            op.notes = (op.notes + "\n" if op.notes else "") + f"[cancel] {payload.reason}"
        self.db.commit()
        self.db.refresh(op)
        return op

    def attach_notes(self, op_uuid: UUID, notes: str, set_pending: bool = False) -> WhatsAppOperation:
        """Adjunta/actualiza las notas (datos de pago) de una op activa.

        Reemplaza `op.notes` (igual semántica que `updateOperationStatus(..., { notes })`
        del bot). Si `set_pending` y la op está QUOTED → la transiciona a PENDING
        (validando expiración), seteando `approved_at`. No-op de estado si ya es PENDING.
        """
        op = self._get_op_or_404(op_uuid)
        if op.status in (WhatsAppOperationStatus.COMPLETED, WhatsAppOperationStatus.CANCELLED):
            raise QuoteServiceError(
                "invalid_status",
                f"No se pueden adjuntar notas a una op en {op.status.value}",
                409,
            )

        op.notes = notes

        if set_pending and op.status == WhatsAppOperationStatus.QUOTED:
            if op.expires_at <= datetime.now(timezone.utc):
                raise QuoteServiceError("quote_expired", "La cotización expiró", 409)
            op.status = WhatsAppOperationStatus.PENDING
            op.approved_at = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(op)
        return op

    # ---------- Completar (genera Transaction) ----------

    def complete_operation(
        self,
        op_uuid: UUID,
        payload: WhatsAppOperationComplete,
        bot_service_user: User,
    ) -> WhatsAppOperation:
        op = self._get_op_or_404(op_uuid)
        if op.status not in (WhatsAppOperationStatus.QUOTED, WhatsAppOperationStatus.PENDING):
            raise QuoteServiceError(
                "invalid_status",
                f"Solo se puede completar una op en QUOTED o PENDING; actual: {op.status.value}",
                409,
            )

        # Flujo sin confirmación de cliente: el operador completa directo desde QUOTED.
        # Completar implica la aprobación (el operador es quien decide proceder). El camino
        # QUOTED→PENDING→COMPLETED sigue válido para flujos que sí confirmen con el cliente.
        if op.status == WhatsAppOperationStatus.QUOTED:
            if op.expires_at <= datetime.now(timezone.utc):
                raise QuoteServiceError("quote_expired", "La cotización expiró", 409)
            op.approved_at = datetime.now(timezone.utc)

        # Crear Transaction reusando el repo existente (que dispara profit splits)
        tx = self._create_transaction_for_op(op, payload, bot_service_user)

        op.status = WhatsAppOperationStatus.COMPLETED
        op.completed_at = datetime.now(timezone.utc)
        op.transaction_id = tx.id

        # Delivery tracking: si la op es venta de USD efectivo y el operador todavía
        # no recibió los billetes, marcar pending_delivery (espejo de la lógica del bot
        # en whatsapp-bot/src/operations.ts createOperationFromPayment).
        cp = op.currency_pair
        from_symbol = cp.from_currency.symbol if cp and cp.from_currency else None
        if payload.pending_delivery and from_symbol == "USD":
            op.delivery_status = WhatsAppDeliveryStatus.PENDING

        if payload.notes:
            op.notes = (op.notes + "\n" if op.notes else "") + payload.notes

        self.db.commit()
        self.db.refresh(op)
        return op

    def _create_transaction_for_op(
        self,
        op: WhatsAppOperation,
        payload: WhatsAppOperationComplete,
        bot_service_user: User,
    ) -> Transaction:
        cp = op.currency_pair
        if cp is None:
            raise QuoteServiceError("invalid_state", "Operation sin currency_pair", 500)

        config_repo = CommissionConfigRepository(self.db)

        total_pct = 0.0
        profit_splits: Optional[list[ProfitSplitCreate]] = None
        commission_config_uuid = payload.commission_config_uuid

        if commission_config_uuid:
            config = config_repo.get_by_uuid(commission_config_uuid)
            if not config:
                raise QuoteServiceError("config_not_found", "Commission config no existe", 404)
            if not config.is_active:
                raise QuoteServiceError("config_inactive", f"Commission config '{config.name}' no activa", 400)
            total_pct = float(config.total_percentage)
            profit_splits = [
                ProfitSplitCreate(user_uuid=split.user.uuid, profit_percentage=float(split.percentage))
                for split in config.splits
            ]

        tx_create = TransactionCreate(
            currency_pair_uuid=cp.uuid,
            from_amount=op.from_amount,
            to_amount=op.to_amount,
            exchange_rate=op.rate_used,
            description=f"WhatsApp op {op.uuid}",
            transaction_type="whatsapp",
            total_profit_percentage=total_pct,
            profit_splits=profit_splits,
            skip_fund=payload.skip_fund,
        )

        tx_repo = TransactionRepository(self.db)
        tx = tx_repo.create_transaction(
            tx_create,
            created_by_user_id=bot_service_user.id,
            currency_pair_id=cp.id,
        )
        return tx

    # ---------- Lookup ----------

    def get_by_uuid(self, op_uuid: UUID) -> Optional[WhatsAppOperation]:
        return self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op_uuid).first()

    def get_active_for_phone(self, phone: str) -> Optional[WhatsAppOperation]:
        return (
            self.db.query(WhatsAppOperation)
            .join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)
            .filter(WhatsAppClient.phone == phone)
            .filter(WhatsAppOperation.status.in_(
                [WhatsAppOperationStatus.QUOTED, WhatsAppOperationStatus.PENDING]
            ))
            .order_by(WhatsAppOperation.created_at.desc())
            .first()
        )

    def list_operations(
        self,
        phone: Optional[str] = None,
        status: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
        delivery_status: Optional[str] = None,
    ) -> list[WhatsAppOperation]:
        q = self.db.query(WhatsAppOperation)
        if phone:
            q = q.join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)\
                 .filter(WhatsAppClient.phone == phone)
        if status:
            try:
                q = q.filter(WhatsAppOperation.status == WhatsAppOperationStatus(status.upper()))
            except ValueError:
                raise QuoteServiceError("invalid_status", f"Status inválido: {status}", 400)
        if delivery_status:
            try:
                q = q.filter(WhatsAppOperation.delivery_status == WhatsAppDeliveryStatus(delivery_status.upper()))
            except ValueError:
                raise QuoteServiceError("invalid_delivery_status", f"delivery_status inválido: {delivery_status}", 400)
        if since:
            q = q.filter(WhatsAppOperation.created_at >= since)
        return q.order_by(WhatsAppOperation.created_at.desc()).limit(limit).all()

    def mark_delivered(self, op_uuid: UUID) -> WhatsAppOperation:
        """Marca como recibidos los USD efectivo de una op con entrega pendiente.

        Espejo de `markDelivered(opId)` del bot: requiere delivery_status==PENDING.
        """
        op = self._get_op_or_404(op_uuid)
        if op.delivery_status != WhatsAppDeliveryStatus.PENDING:
            raise QuoteServiceError(
                "invalid_status",
                "La operación no tiene entrega de USD pendiente",
                409,
            )
        op.delivery_status = WhatsAppDeliveryStatus.RECEIVED
        op.delivered_at = datetime.now(timezone.utc)
        self.db.commit()
        self.db.refresh(op)
        return op

    # ---------- Escenario / grupo / receptor del entrante ----------

    def set_scenario(self, op_uuid: UUID, payload: WhatsAppOperationScenarioUpdate) -> WhatsAppOperation:
        """
        Setea/edita el escenario, el FundGroup y el receptor del pago entrante de una op.
        Usado tanto por el bot (clasificación automática, resuelve el grupo por `group_jid`)
        como por el front (edición manual, resuelve por `fund_group_uuid`/`received_by_user_uuid`).
        """
        op = self._get_op_or_404(op_uuid)

        if payload.scenario is not None:
            op.scenario = WhatsAppOperationScenario(payload.scenario)

        # Grupo: por uuid (front) o por JID del grupo de WhatsApp (bot). clear_fund_group lo limpia.
        if payload.clear_fund_group:
            op.fund_group_id = None
        elif payload.fund_group_uuid is not None:
            group = self.db.query(FundGroup).filter(FundGroup.uuid == str(payload.fund_group_uuid)).first()
            if group is None:
                raise QuoteServiceError("fund_group_not_found", "FundGroup no encontrado", 404)
            op.fund_group_id = group.id
        elif payload.group_jid:
            group = self.db.query(FundGroup).filter(FundGroup.whatsapp_group_jid == payload.group_jid).first()
            if group is None:
                raise QuoteServiceError(
                    "fund_group_not_found",
                    f"No hay FundGroup asociado al grupo {payload.group_jid}",
                    404,
                )
            op.fund_group_id = group.id

        # Receptor del entrante. clear_received_by lo limpia (vuelve a operador).
        if payload.clear_received_by:
            op.received_by_user_id = None
        elif payload.received_by_user_uuid is not None:
            user = self.db.query(User).filter(User.uuid == str(payload.received_by_user_uuid)).first()
            if user is None:
                raise QuoteServiceError("user_not_found", "Usuario receptor no encontrado", 404)
            op.received_by_user_id = user.id

        self.db.commit()
        self.db.refresh(op)
        return op

    def find_operation_for_group_forwarding(self, client_phone: str) -> Optional[WhatsAppOperation]:
        """
        Resuelve la operación a etiquetar cuando se reenvía un comprobante al grupo
        (escenario ZELLE_DIRECT). Prioriza la op activa del cliente; si no hay, la más
        reciente (cualquier estado).
        """
        active = self.get_active_for_phone(client_phone)
        if active is not None:
            return active
        return (
            self.db.query(WhatsAppOperation)
            .join(WhatsAppClient, WhatsAppClient.id == WhatsAppOperation.client_id)
            .filter(WhatsAppClient.phone == client_phone)
            .order_by(WhatsAppOperation.created_at.desc())
            .first()
        )

    def list_partners(self) -> list[dict]:
        """
        Lista socios: miembros de fondo con `whatsapp_phone` seteado. El bot usa esto para
        detectar mensajes de socios (ej. Jean) y clasificar el escenario VIA_PARTNER.
        """
        members = (
            self.db.query(FundGroupMember)
            .filter(FundGroupMember.whatsapp_phone.isnot(None))
            .all()
        )
        result: list[dict] = []
        for m in members:
            group = m.group
            result.append({
                "whatsapp_phone": m.whatsapp_phone,
                "user_uuid": m.user.uuid if m.user else None,
                "username": m.user.username if m.user else None,
                "group_uuid": group.uuid if group else None,
                "group_name": group.name if group else None,
                "group_jid": group.whatsapp_group_jid if group else None,
            })
        return result

    def get_stats(self) -> dict:
        """Conteos por estado + completados hoy (espejo de `getOperationStats` del bot)."""
        from sqlalchemy import func as safunc

        rows = (
            self.db.query(WhatsAppOperation.status, safunc.count(WhatsAppOperation.id))
            .group_by(WhatsAppOperation.status)
            .all()
        )
        counts = {s.value: 0 for s in WhatsAppOperationStatus}
        for st, cnt in rows:
            counts[st.value] = cnt

        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today = (
            self.db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.status == WhatsAppOperationStatus.COMPLETED,
                WhatsAppOperation.completed_at >= today_start,
            )
            .count()
        )
        return {
            "pending": counts["PENDING"],
            "completed": counts["COMPLETED"],
            "quoted": counts["QUOTED"],
            "cancelled": counts["CANCELLED"],
            "completed_today": completed_today,
        }

    # ---------- Helpers ----------

    def _get_op_or_404(self, op_uuid: UUID) -> WhatsAppOperation:
        op = self.get_by_uuid(op_uuid)
        if op is None:
            raise QuoteServiceError("not_found", f"Operation {op_uuid} no encontrada", 404)
        return op
