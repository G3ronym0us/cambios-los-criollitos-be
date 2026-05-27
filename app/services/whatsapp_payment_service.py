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

from sqlalchemy.orm import Session

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppDeliveryStatus,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.models.whatsapp_payment import WhatsAppIncomingPayment, WhatsAppOutgoingPayment
from app.repositories.currency_pair_repository import CurrencyPairRepository
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

    def _client_name(self, phone: str) -> Optional[str]:
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
        return client.display_name if client else None

    def _with_name(self, payment) -> dict:
        d = payment.dict()
        d["client_name"] = self._client_name(payment.client_phone)
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
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

    def list_payments(self, table: str, limit: int = 200) -> list[dict]:
        Model = self._model(table)
        rows = (
            self.db.query(Model, WhatsAppClient.display_name)
            .outerjoin(WhatsAppClient, WhatsAppClient.phone == Model.client_phone)
            .order_by(Model.created_at.desc())
            .limit(limit)
            .all()
        )
        out = []
        for payment, display_name in rows:
            d = payment.dict()
            d["client_name"] = display_name
            out.append(d)
        return out

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

    def set_irrelevant(self, payment_id: int, is_irrelevant: bool) -> dict:
        row = self._get_or_404("outgoing", payment_id)
        row.is_irrelevant = is_irrelevant
        if is_irrelevant:
            row.whatsapp_operation_id = None
        self.db.commit()
        self.db.refresh(row)
        return self._with_name(row)

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
