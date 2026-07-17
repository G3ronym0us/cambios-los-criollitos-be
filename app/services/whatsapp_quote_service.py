"""
Servicio orquestador del ciclo de vida de una operación de WhatsApp.

Flujo:
  create_quote   -> QUOTED sin datos / PENDING con datos de pago
  approve_quote  -> QUOTED -> PENDING
  cancel_op      -> ... -> CANCELLED
  complete_op    -> PENDING -> COMPLETED y sincroniza su Transaction

El servicio nunca habla con HTTP; recibe Sessions y devuelve modelos.
La capa router lo expone vía /whatsapp/...
"""

from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.currency_pair import CurrencyPair
from app.models.fund import FundGroup, FundGroupMember
from app.models.transaction import Transaction, TransactionProfitSplit, TransactionStatus
from app.models.user import User
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
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
    WhatsAppOperationUpdate,
)
from app.services.bcv_service import get_cached_bcv_rate
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver, apply_rounding


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

        # El bot puede enviar el monto USD original de una cotización anclada al
        # BCV. El `amount` recibido ya es el monto efectivo convertido a VES;
        # este campo se conserva como referencia y para mostrarlo en el panel.
        bcv_usd: Optional[float] = payload.bcv_usd

        # Compatibilidad del path backend USDT↔VES: si no vino una referencia
        # explícita y el cliente no está autorizado, usar el BCV cacheado.
        bcv_rate: Optional[float] = None
        if (
            bcv_usd is None
            and not client.is_usdt_authorized
            and "USDT" in (payload.from_currency, payload.to_currency)
            and "VES" in (payload.from_currency, payload.to_currency)
        ):
            bcv_rate = get_cached_bcv_rate(self.db)
            # Compatibilidad con operaciones creadas por este path histórico,
            # donde el campo guardaba la tasa BCV en vez del monto de referencia.
            bcv_usd = bcv_rate

        entry = self.resolver.get_rate_entry_for_pair(payload.from_currency, payload.to_currency)
        if entry is None and bcv_rate is None:
            raise QuoteServiceError(
                "rate_not_available",
                f"No hay tasa disponible para {payload.from_currency}/{payload.to_currency}",
                422,
            )

        # Cuando entramos por el path BCV legacy, sobreescribimos la tasa:
        # si el pedido es USDT->VES usamos directo; si es VES->USDT, inverso.
        if bcv_rate is not None and entry is None:
            if payload.from_currency == "USDT" and payload.to_currency == "VES":
                rate = bcv_rate
                inverse_percentage = False
                base_rate = bcv_rate
                base_percentage = None
            else:
                rate = bcv_rate
                inverse_percentage = True
                base_rate = bcv_rate
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

        # Redondeo configurable del par (modo RATE o AMOUNT). No-op si el par no lo define.
        from_amount, to_amount, rate, inverse_percentage = self._apply_pair_rounding(
            currency_pair,
            payload.from_currency,
            payload.to_currency,
            payload.amount_side,
            from_amount,
            to_amount,
            rate,
            inverse_percentage,
        )

        now = datetime.now(timezone.utc)
        has_payment_data = bool(payload.notes and payload.notes.strip())
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
            # Monto + datos de pago ya constituye una operación interna en curso.
            # Una cotización sin datos permanece QUOTED hasta recibirlos.
            status=(
                WhatsAppOperationStatus.PENDING
                if has_payment_data
                else WhatsAppOperationStatus.QUOTED
            ),
            notes=payload.notes,
            quoted_at=now,
            approved_at=now if has_payment_data else None,
            expires_at=now + timedelta(minutes=QUOTE_TTL_MINUTES),
        )
        self.db.add(op)
        self.db.commit()
        self.db.refresh(op)
        return op

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
        self._sync_linked_transaction(op)
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
        self._sync_linked_transaction(op)
        if payload.reason:
            op.notes = (op.notes + "\n" if op.notes else "") + f"[cancel] {payload.reason}"
        self.db.commit()
        self.db.refresh(op)
        return op

    def restore_quote(self, op_uuid: UUID) -> WhatsAppOperation:
        """Revierte una cancelación reciente: CANCELLED -> QUOTED (refresca expires_at).

        Lo usa el bot cuando detecta que una "corrección de monto" era en realidad
        una operación aparte: la op que se había cancelado al asumir la corrección
        debe volver a estar activa. Idempotente si ya está QUOTED.
        """
        op = self._get_op_or_404(op_uuid)
        if op.status == WhatsAppOperationStatus.QUOTED:
            return op
        if op.status != WhatsAppOperationStatus.CANCELLED:
            raise QuoteServiceError(
                "invalid_status",
                f"Solo se puede restaurar una op CANCELLED, no {op.status.value}",
                409,
            )
        now = datetime.now(timezone.utc)
        op.status = WhatsAppOperationStatus.QUOTED
        op.cancelled_at = None
        op.expires_at = now + timedelta(minutes=QUOTE_TTL_MINUTES)
        self._sync_linked_transaction(op)
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
            self._sync_linked_transaction(op)

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

        op.status = WhatsAppOperationStatus.COMPLETED
        op.completed_at = datetime.now(timezone.utc)
        # Crea la transacción si aún no existe o actualiza la que nació con el fondo.
        tx = self._create_transaction_for_op(op, payload, bot_service_user)
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

        # El margen aplicado en la cotización es la ganancia total real de esta
        # operación. La configuración opcional define cómo se reparte, no sustituye
        # el margen que se le cotizó al cliente.
        total_pct = float(op.applied_percentage or 0.0)
        profit_splits: Optional[list[ProfitSplitCreate]] = None
        commission_config_uuid = payload.commission_config_uuid
        config = None

        if commission_config_uuid:
            config = config_repo.get_by_uuid(commission_config_uuid)
            if not config:
                raise QuoteServiceError("config_not_found", "Commission config no existe", 404)
            if not config.is_active:
                raise QuoteServiceError("config_inactive", f"Commission config '{config.name}' no activa", 400)
            config_total = float(config.total_percentage)
            if op.applied_percentage is None:
                total_pct = config_total
                op.applied_percentage = total_pct

            # Mantiene las proporciones del reparto configurado, ajustadas al margen
            # efectivo de la operación. Con ganancia 0 no se crean splits para evitar
            # una división por cero al calcular sus montos.
            if total_pct > 0 and config_total > 0:
                profit_splits = [
                    ProfitSplitCreate(
                        user_uuid=split.user.uuid,
                        profit_percentage=(float(split.percentage) / config_total) * total_pct,
                    )
                    for split in config.splits
                ]

        if op.transaction_id is not None:
            tx = self.db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
            if tx is None:
                raise QuoteServiceError("transaction_not_found", "Transaction vinculada no encontrada", 409)

            self._sync_linked_transaction(op)
            if config is not None:
                self.db.query(TransactionProfitSplit).filter(
                    TransactionProfitSplit.transaction_id == tx.id
                ).delete(synchronize_session=False)
                profit_amount = float(op.to_amount) * total_pct / 100
                for split_data, config_split in zip(profit_splits or [], config.splits):
                    ratio = split_data.profit_percentage / total_pct if total_pct else 0
                    self.db.add(
                        TransactionProfitSplit(
                            transaction_id=tx.id,
                            user_id=config_split.user.id,
                            profit_percentage=split_data.profit_percentage,
                            profit_amount=profit_amount * ratio,
                            settlement_currency=config_split.user.preferred_settlement_currency,
                        )
                    )
            return tx

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
            status=op.status.value.lower(),
        )

        tx_repo = TransactionRepository(self.db)
        tx = tx_repo.create_transaction(
            tx_create,
            created_by_user_id=bot_service_user.id,
            currency_pair_id=cp.id,
        )
        return tx

    def _sync_linked_transaction(self, op: WhatsAppOperation) -> None:
        """Mantiene la transacción derivada como espejo contable de la operación."""
        if op.transaction_id is None:
            return

        tx = self.db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
        if tx is None:
            raise QuoteServiceError("transaction_not_found", "Transaction vinculada no encontrada", 409)

        previous_profit = float(tx.profit_amount or 0)
        previous_total = float(tx.total_profit_percentage or 0)
        total_pct = float(op.applied_percentage or 0)
        profit_amount = float(op.to_amount) * total_pct / 100

        tx.currency_pair_id = op.currency_pair_id
        tx.from_amount = op.from_amount
        tx.to_amount = op.to_amount
        tx.exchange_rate = op.rate_used
        tx.total_profit_percentage = total_pct
        tx.profit_amount = profit_amount
        tx.status = TransactionStatus[op.status.name]
        tx.completed_at = op.completed_at if op.status == WhatsAppOperationStatus.COMPLETED else None

        if tx.profit_splits and previous_total > 0:
            profit_scale = profit_amount / previous_profit if previous_profit else None
            all_splits_have_usdt = True
            total_profit_usdt = 0.0
            for split in tx.profit_splits:
                ratio = float(split.profit_percentage) / previous_total
                split.profit_percentage = total_pct * ratio
                split.profit_amount = profit_amount * ratio
                if split.profit_amount_usdt is not None and profit_scale is not None:
                    split.profit_amount_usdt *= profit_scale
                    if split.settlement_currency and split.settlement_currency.upper() in ("USD", "USDT"):
                        split.settlement_amount = split.profit_amount_usdt
                    total_profit_usdt += split.profit_amount_usdt
                else:
                    all_splits_have_usdt = False
            tx.profit_amount_usdt = total_profit_usdt if all_splits_have_usdt else None

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

    def mark_delivered(self, op_uuid: UUID, completed_by_user: User) -> WhatsAppOperation:
        """Recibe los USD físicos y cierra contablemente la operación.

        Las operaciones USD→VES creadas desde el pago saliente permanecen PENDING
        hasta que el cliente entrega el efectivo. Recibirlo debe completar la op y
        crear (o sincronizar) su transacción dentro de la misma acción.
        """
        op = self._get_op_or_404(op_uuid)
        if op.delivery_status != WhatsAppDeliveryStatus.PENDING:
            raise QuoteServiceError(
                "invalid_status",
                "La operación no tiene entrega de USD pendiente",
                409,
            )
        if op.status == WhatsAppOperationStatus.CANCELLED:
            raise QuoteServiceError(
                "invalid_operation_status",
                f"No se puede completar una operación {op.status.value}",
                409,
            )

        now = datetime.now(timezone.utc)
        op.delivery_status = WhatsAppDeliveryStatus.RECEIVED
        op.delivered_at = now
        if op.status != WhatsAppOperationStatus.COMPLETED:
            op.status = WhatsAppOperationStatus.COMPLETED
            op.completed_at = now

        tx = self._create_transaction_for_op(
            op,
            WhatsAppOperationComplete(),
            completed_by_user,
        )
        op.transaction_id = tx.id
        tx.completed_at = op.completed_at
        self.db.commit()
        self.db.refresh(op)
        return op

    def repair_received_deliveries(self, completed_by_user: User) -> list[str]:
        """Repara entregas RECEIVED que quedaron PENDING por la lógica anterior."""
        operations = (
            self.db.query(WhatsAppOperation)
            .filter(
                WhatsAppOperation.delivery_status == WhatsAppDeliveryStatus.RECEIVED,
                WhatsAppOperation.status.in_((
                    WhatsAppOperationStatus.QUOTED,
                    WhatsAppOperationStatus.PENDING,
                    WhatsAppOperationStatus.COMPLETED,
                )),
                or_(
                    WhatsAppOperation.status != WhatsAppOperationStatus.COMPLETED,
                    WhatsAppOperation.transaction_id.is_(None),
                ),
            )
            .order_by(WhatsAppOperation.delivered_at.asc())
            .all()
        )
        repaired: list[str] = []
        for op in operations:
            if op.status != WhatsAppOperationStatus.COMPLETED:
                op.status = WhatsAppOperationStatus.COMPLETED
                op.completed_at = op.delivered_at or datetime.now(timezone.utc)
            elif op.completed_at is None:
                op.completed_at = op.delivered_at or datetime.now(timezone.utc)
            tx = self._create_transaction_for_op(
                op,
                WhatsAppOperationComplete(),
                completed_by_user,
            )
            op.transaction_id = tx.id
            tx.completed_at = op.completed_at
            self.db.commit()
            repaired.append(str(op.uuid))
        return repaired

    # ---------- Escenario / grupo / receptor del entrante ----------

    def _apply_scenario(
        self,
        op: WhatsAppOperation,
        payload: WhatsAppOperationScenarioUpdate,
    ) -> None:
        if payload.scenario is not None:
            op.scenario = WhatsAppOperationScenario(payload.scenario)

        # Grupo: por uuid (front) o por JID del grupo de WhatsApp (bot). clear_fund_group lo limpia.
        if payload.clear_fund_group:
            op.fund_group_id = None
        elif payload.fund_group_uuid is not None:
            group = (
                self.db.query(FundGroup)
                .filter(FundGroup.uuid == str(payload.fund_group_uuid))
                .first()
            )
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

        # Cliente anónimo: en VIA_PARTNER el "cliente" no debe ser el socio/gestor que reportó
        # el cambio. Se reasigna la op a un cliente anónimo dedicado y determinístico por
        # receptor del entrante (o grupo), para no atribuirle operaciones al número del socio.
        if payload.anonymize_client:
            key = op.received_by_user_id or op.fund_group_id or 0
            label = "socio"
            if op.received_by_user_id:
                ru = self.db.query(User).filter(User.id == op.received_by_user_id).first()
                if ru and ru.username:
                    label = ru.username
            anon = self.upsert_client(f"anon:partner:{key}", f"Anónimo (vía {label})")
            op.client_id = anon.id

    def set_scenario(
        self,
        op_uuid: UUID,
        payload: WhatsAppOperationScenarioUpdate,
        operator: User,
    ) -> WhatsAppOperation:
        """
        Setea/edita el escenario, el FundGroup y el receptor del pago entrante de una op.
        Usado tanto por el bot (clasificación automática, resuelve el grupo por `group_jid`)
        como por el front (edición manual, resuelve por `fund_group_uuid`/`received_by_user_uuid`).
        """
        op = self._get_op_or_404(op_uuid)
        self._apply_scenario(op, payload)
        if op.fund_group_id is not None and op.transaction_id is None:
            tx = self._create_transaction_for_op(op, WhatsAppOperationComplete(), operator)
            op.transaction_id = tx.id
        else:
            self._sync_linked_transaction(op)
        self.db.commit()
        self.db.refresh(op)
        return op

    def _assign_client(
        self,
        op: WhatsAppOperation,
        phone: str,
        display_name: Optional[str],
        update_display_name: bool,
    ) -> None:
        client = self.upsert_client(phone)
        if update_display_name:
            client.display_name = display_name
        op.client_id = client.id
        op.client = client

        # El operador afirma "esta operación es de este cliente": los comprobantes ya
        # vinculados heredan el teléfono (mismo criterio que set_operation en pagos).
        for Model in (WhatsAppIncomingPayment, WhatsAppOutgoingPayment):
            self.db.query(Model).filter(Model.whatsapp_operation_id == op.id).update(
                {"client_phone": client.phone}, synchronize_session=False
            )

    def update_operation(
        self,
        op_uuid: UUID,
        payload: WhatsAppOperationUpdate,
        operator: User,
    ) -> WhatsAppOperation:
        """Actualiza par, cliente y escenario en una sola transacción."""
        op = self._get_op_or_404(op_uuid)
        fields_set = payload.model_fields_set

        if payload.currency_pair_uuid is not None:
            pair = (
                self.db.query(CurrencyPair)
                .filter(CurrencyPair.uuid == payload.currency_pair_uuid)
                .first()
            )
            if pair is None:
                raise QuoteServiceError(
                    "currency_pair_not_found",
                    "Par de monedas no encontrado",
                    404,
                )
            # Corrección administrativa: conserva montos y tasa históricos.
            op.currency_pair_id = pair.id
            op.currency_pair = pair

        if "applied_percentage" in fields_set and payload.applied_percentage is not None:
            if op.status == WhatsAppOperationStatus.COMPLETED:
                raise QuoteServiceError(
                    "completed_margin_is_locked",
                    "No se puede cambiar el margen de una operación completada porque su transacción ya fue generada",
                    409,
                )

            previous_percentage = op.applied_percentage or 0.0
            previous_factor = 1 - previous_percentage / 100
            if previous_factor <= 0:
                raise QuoteServiceError(
                    "invalid_previous_margin",
                    "No se pudo reconstruir la tasa base desde el margen anterior",
                    409,
                )

            # Reconstruye la tasa cruda histórica y aplica el margen corregido.
            base_rate = (
                op.rate_used * previous_factor
                if op.inverse_percentage
                else op.rate_used / previous_factor
            )
            new_rate = self.resolver.rate_with_margin(
                base_rate,
                payload.applied_percentage,
                op.inverse_percentage,
            )
            if new_rate is None:
                raise QuoteServiceError("invalid_margin", "Margen inválido", 422)

            op.rate_used = new_rate
            op.applied_percentage = payload.applied_percentage
            if op.amount_side == WhatsAppAmountSide.SEND:
                op.to_amount = self.resolver.apply_rate(
                    op.from_amount,
                    new_rate,
                    op.inverse_percentage,
                )
            else:
                op.from_amount = self.resolver.apply_rate(
                    op.to_amount,
                    new_rate,
                    not op.inverse_percentage,
                )

            # Re-aplica el redondeo del par sobre los montos recalculados, con la misma
            # convención canónica que usa el resto del sistema (op.from == pair.from,
            # ver WhatsAppOperation.dict()). El lado que el cliente fijó (amount_side)
            # se preserva; el lado calculado se redondea igual que en create_quote.
            cp = op.currency_pair
            if cp is not None and cp.from_currency and cp.to_currency:
                (
                    op.from_amount,
                    op.to_amount,
                    op.rate_used,
                    op.inverse_percentage,
                ) = self._apply_pair_rounding(
                    cp,
                    cp.from_currency.symbol,
                    cp.to_currency.symbol,
                    op.amount_side.value,
                    op.from_amount,
                    op.to_amount,
                    op.rate_used,
                    op.inverse_percentage,
                )

        if "client_display_name" in fields_set and payload.client_phone is None:
            raise QuoteServiceError(
                "client_phone_required",
                "El teléfono es requerido para actualizar el cliente",
                422,
            )
        if payload.client_phone is not None:
            self._assign_client(
                op,
                payload.client_phone,
                payload.client_display_name,
                "client_display_name" in fields_set,
            )

        self._apply_scenario(op, payload)
        if op.fund_group_id is not None and op.transaction_id is None:
            tx = self._create_transaction_for_op(op, WhatsAppOperationComplete(), operator)
            op.transaction_id = tx.id
        else:
            self._sync_linked_transaction(op)
        self.db.commit()
        self.db.refresh(op)
        return op

    def update_status(
        self,
        op_uuid: UUID,
        requested_status: str,
        operator: User,
    ) -> WhatsAppOperation:
        """Cambio administrativo de estado preservando invariantes contables."""
        op = self._get_op_or_404(op_uuid)
        target = WhatsAppOperationStatus(requested_status)

        if op.status == target:
            return op
        if op.status == WhatsAppOperationStatus.COMPLETED:
            raise QuoteServiceError(
                "completed_status_is_terminal",
                "Una operación completada no puede volver a otro estado porque ya tiene una transacción contable",
                409,
            )

        now = datetime.now(timezone.utc)
        if target == WhatsAppOperationStatus.COMPLETED:
            if op.transaction_id is None:
                tx = self._create_transaction_for_op(
                    op,
                    WhatsAppOperationComplete(),
                    operator,
                )
                op.transaction_id = tx.id
            op.status = target
            op.completed_at = now
            op.cancelled_at = None
        elif target == WhatsAppOperationStatus.CANCELLED:
            op.status = target
            op.cancelled_at = now
            op.completed_at = None
        elif target == WhatsAppOperationStatus.PENDING:
            op.status = target
            op.approved_at = op.approved_at or now
            op.cancelled_at = None
            op.completed_at = None
        else:
            op.status = WhatsAppOperationStatus.QUOTED
            op.approved_at = None
            op.cancelled_at = None
            op.completed_at = None
            op.expires_at = now + timedelta(minutes=QUOTE_TTL_MINUTES)

        self._sync_linked_transaction(op)

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
                "is_fund_manager": bool(m.is_fund_manager),
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

    def _apply_pair_rounding(
        self,
        pair: CurrencyPair,
        quote_from: str,
        quote_to: str,
        amount_side: str,
        from_amount: float,
        to_amount: float,
        rate: float,
        inverse: bool,
    ) -> tuple[float, float, float, bool]:
        """Aplica el redondeo configurado en el par a la cotización recién calculada.

        Devuelve `(from_amount, to_amount, rate_used, inverse)` ya ajustados. Si el
        par no define redondeo (o el config es inválido) devuelve los valores intactos.

        - `quote_from`/`quote_to`: monedas EN LA DIRECCIÓN de esta cotización (las del
          payload), que pueden ser el par canónico o su inverso.
        - Modo RATE: redondea la tasa efectiva (unidades de `to` por 1 de `from`) y
          recalcula el lado calculado; se persiste esa tasa efectiva como no-inversa.
        - Modo AMOUNT: redondea el monto de la moneda canónica configurada, pero solo
          cuando esa moneda es el lado CALCULADO (no el input del cliente).
        """
        mode = pair.rounding_mode if pair is not None else None
        if not mode:
            return from_amount, to_amount, rate, inverse
        step = float(pair.rounding_step) if pair.rounding_step is not None else 0.0
        if step <= 0:
            return from_amount, to_amount, rate, inverse
        direction = pair.rounding_direction or "UP"
        quote_from = (quote_from or "").upper()
        quote_to = (quote_to or "").upper()

        if mode == "RATE":
            eff = self.resolver.apply_rate(1.0, rate, inverse)  # `to` por 1 de `from`
            eff_round = apply_rounding(eff, step, direction)
            if eff_round <= 0:
                return from_amount, to_amount, rate, inverse
            if amount_side == "SEND":
                to_amount = from_amount * eff_round
            else:  # RECEIVE
                from_amount = to_amount / eff_round
            return from_amount, to_amount, eff_round, False

        if mode == "AMOUNT":
            side = pair.rounding_amount_side
            if side is None or not (pair.from_currency and pair.to_currency):
                return from_amount, to_amount, rate, inverse
            target = (pair.from_currency.symbol if side == "FROM" else pair.to_currency.symbol).upper()
            # SEND calcula el `to`; RECEIVE calcula el `from`. Solo redondeamos si la
            # moneda objetivo es justamente ese lado calculado.
            if amount_side == "SEND" and target == quote_to:
                to_amount = apply_rounding(to_amount, step, direction)
            elif amount_side == "RECEIVE" and target == quote_from:
                from_amount = apply_rounding(from_amount, step, direction)

        return from_amount, to_amount, rate, inverse

    def _get_op_or_404(self, op_uuid: UUID) -> WhatsAppOperation:
        op = self.get_by_uuid(op_uuid)
        if op is None:
            raise QuoteServiceError("not_found", f"Operation {op_uuid} no encontrada", 404)
        return op
