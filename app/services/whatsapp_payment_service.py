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

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.fund import FundGroup, FundMovement, FundMovementType
from app.models.user import User
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.repositories.fund_repository import FundRepository
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService


EDITABLE_FIELDS = ["provider", "amount", "currency", "bank_from", "bank_to", "identification", "phone_to", "reference"]
QUOTE_TTL_MINUTES = 30


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
    ) -> dict:
        """Página de pagos para el front: búsqueda + clasificación server-side. Devuelve {items, total}."""
        Model = self._model(table)
        q = self._payments_base_query(Model)

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
                q = q.filter(Model.is_personal_expense.is_(False), Model.is_irrelevant.is_(False))
            elif out_class == "UNLINKED":
                q = q.filter(
                    Model.is_personal_expense.is_(False),
                    Model.is_irrelevant.is_(False),
                    Model.whatsapp_operation_id.is_(None),
                )

        total = q.count()
        rows = q.order_by(Model.created_at.desc()).limit(limit).offset(offset).all()
        items = [self._row_to_dict(*r) for r in rows]
        if table == "incoming" and items:
            self._attach_deposits(items)
        return {"items": items, "total": total}

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

    def set_operation(self, table: str, payment_id: int, operation_uuid: Optional[UUID]) -> dict:
        row = self._get_or_404(table, payment_id)
        if operation_uuid is not None:
            op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == operation_uuid).first()
            if op is None:
                raise QuoteServiceError("op_not_found", f"Operation {operation_uuid} no encontrada", 404)
            row.whatsapp_operation_id = op.id
            # Sincroniza client_phone al de la op (el operador afirma "este pago es de este cliente").
            if op.client and op.client.phone:
                row.client_phone = op.client.phone
        else:
            row.whatsapp_operation_id = None
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

    def set_personal_expense(self, payment_id: int, is_personal: bool, description: Optional[str]) -> dict:
        row = self._get_or_404("outgoing", payment_id)
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
        row.is_irrelevant = is_irrelevant
        if is_irrelevant:
            row.irrelevant_description = description
            row.whatsapp_operation_id = None
        else:
            row.irrelevant_description = None
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

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
    ) -> dict:
        row = self._get_or_404(table, payment_id)
        if from_amount <= 0 or to_amount <= 0:
            raise QuoteServiceError("invalid_amount", "Montos deben ser > 0", 400)

        pair_symbol = f"{from_currency}-{to_currency}"
        pair = self.pair_repo.get_by_symbol(pair_symbol)
        if pair is None:
            pair = self.pair_repo.get_by_symbol(f"{to_currency}-{from_currency}")
        if pair is None:
            raise QuoteServiceError("pair_not_found", f"No existe currency pair para {from_currency}/{to_currency}", 404)

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
            amount_side=WhatsAppAmountSide.SEND,
            status=WhatsAppOperationStatus.PENDING,
            delivery_status=WhatsAppDeliveryStatus.PENDING if track_delivery else None,
            quoted_at=now,
            expires_at=now + timedelta(minutes=QUOTE_TTL_MINUTES),
            approved_at=now,
        )
        self.db.add(op)
        self.db.flush()
        row.whatsapp_operation_id = op.id
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
