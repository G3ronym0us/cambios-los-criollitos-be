"""
Servicio de saldo a favor del cliente (ledger `whatsapp_balance_entries`).

Caso de uso: el cliente envía un Zelle grande (ej. 200) pero quiere que se le
pague en varios abonos. El entrante se ACREDITA como saldo en USD; cada abono es
una operación nueva cotizada a la tasa del día que DEBITA su `from_amount`.
El saldo nunca congela tasa: solo lleva cuántos USD quedan por pagar.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation
from app.models.whatsapp_payment import WhatsAppIncomingPayment
from app.services.whatsapp_payment_service import settlement_currency
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService

# Tolerancia contable en USD para "saldo suficiente" y "saldo en cero".
BALANCE_EPSILON = 0.01


class WhatsAppBalanceService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- Lookups ----------

    def _client_by_uuid(self, client_uuid: UUID) -> WhatsAppClient:
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.uuid == str(client_uuid)).first()
        if client is None:
            raise QuoteServiceError("client_not_found", f"Cliente {client_uuid} no encontrado", 404)
        return client

    def _client_by_phone(self, phone: str) -> WhatsAppClient:
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == phone).first()
        if client is None:
            raise QuoteServiceError("client_not_found", f"Cliente {phone} no encontrado", 404)
        return client

    def _op_by_uuid(self, op_uuid: UUID) -> WhatsAppOperation:
        op = self.db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(op_uuid)).first()
        if op is None:
            raise QuoteServiceError("op_not_found", f"Operation {op_uuid} no encontrada", 404)
        return op

    # ---------- Consulta ----------

    def get_balance(self, client_id: int) -> float:
        signed = case(
            (WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT, WhatsAppBalanceEntry.amount),
            else_=-WhatsAppBalanceEntry.amount,
        )
        total = (
            self.db.query(func.coalesce(func.sum(signed), 0.0))
            .filter(WhatsAppBalanceEntry.client_id == client_id)
            .scalar()
        )
        return round(float(total), 2)

    def balances_by_client_ids(self, client_ids: list[int]) -> dict[int, float]:
        """Saldos agregados para un listado de clientes (una sola query)."""
        if not client_ids:
            return {}
        signed = case(
            (WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT, WhatsAppBalanceEntry.amount),
            else_=-WhatsAppBalanceEntry.amount,
        )
        rows = (
            self.db.query(WhatsAppBalanceEntry.client_id, func.sum(signed))
            .filter(WhatsAppBalanceEntry.client_id.in_(client_ids))
            .group_by(WhatsAppBalanceEntry.client_id)
            .all()
        )
        return {client_id: round(float(total), 2) for client_id, total in rows}

    def summary(self, client: WhatsAppClient, limit: int = 100) -> dict:
        entries = (
            self.db.query(WhatsAppBalanceEntry)
            .filter(WhatsAppBalanceEntry.client_id == client.id)
            .order_by(WhatsAppBalanceEntry.created_at.desc(), WhatsAppBalanceEntry.id.desc())
            .limit(limit)
            .all()
        )
        return {
            "client_uuid": client.uuid,
            "client_phone": client.phone,
            "balance": self.get_balance(client.id),
            "currency": "USD",
            "entries": [e.dict() for e in entries],
        }

    def summary_by_uuid(self, client_uuid: UUID) -> dict:
        return self.summary(self._client_by_uuid(client_uuid))

    def summary_by_phone(self, phone: str) -> dict:
        return self.summary(self._client_by_phone(phone))

    # ---------- Crédito (entrante → saldo) ----------

    def credit_from_incoming(
        self,
        payment_id: int,
        amount: Optional[float] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        """
        Acredita un pago ENTRANTE (ej. Zelle 200) como saldo a favor del cliente.
        Idempotente por pago: un entrante solo puede acreditarse una vez.
        """
        payment = (
            self.db.query(WhatsAppIncomingPayment)
            .filter(WhatsAppIncomingPayment.id == payment_id)
            .first()
        )
        if payment is None:
            raise QuoteServiceError("not_found", f"Pago incoming/{payment_id} no encontrado", 404)

        existing = (
            self.db.query(WhatsAppBalanceEntry)
            .filter(
                WhatsAppBalanceEntry.incoming_payment_id == payment_id,
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.CREDIT,
            )
            .first()
        )
        if existing is not None:
            raise QuoteServiceError(
                "already_credited", f"El pago incoming/{payment_id} ya fue acreditado como saldo", 409
            )

        amount = amount if amount is not None else payment.amount
        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto a acreditar debe ser > 0", 400)

        # Solo métodos que liquidan en USD (ZELLE/PAYPAL/USD) alimentan el saldo USD.
        settle = settlement_currency(payment.currency)
        if settle not in ("USD", ""):
            raise QuoteServiceError(
                "unsupported_currency",
                f"Solo se acreditan pagos en USD/ZELLE/PAYPAL (recibido: {payment.currency})",
                400,
            )

        if payment.client_phone.endswith("@g.us"):
            raise QuoteServiceError("invalid_client", "No se puede acreditar saldo a un grupo", 400)
        client = WhatsAppQuoteService(self.db).upsert_client(payment.client_phone)

        entry = WhatsAppBalanceEntry(
            client_id=client.id,
            entry_type=WhatsAppBalanceEntryType.CREDIT,
            amount=amount,
            currency="USD",
            incoming_payment_id=payment.id,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        return entry.dict()

    # ---------- Débito (abono contra el saldo) ----------

    def debit_for_operation(
        self,
        op_uuid: UUID,
        amount: Optional[float] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        """
        Debita del saldo del cliente el lado USD de una operación de abono
        (default: `from_amount`). Idempotente por operación: una op solo puede
        consumir saldo una vez. Valida saldo suficiente (±0.01).
        """
        op = self._op_by_uuid(op_uuid)
        if op.client is None:
            raise QuoteServiceError("client_not_found", "La operación no tiene cliente", 404)

        existing = (
            self.db.query(WhatsAppBalanceEntry)
            .filter(
                WhatsAppBalanceEntry.whatsapp_operation_id == op.id,
                WhatsAppBalanceEntry.entry_type == WhatsAppBalanceEntryType.DEBIT,
            )
            .first()
        )
        if existing is not None:
            raise QuoteServiceError(
                "already_debited", f"La operación {op_uuid} ya consumió saldo", 409
            )

        if amount is None:
            cp = op.currency_pair
            from_symbol = cp.from_currency.symbol if cp and cp.from_currency else None
            if settlement_currency(from_symbol) != "USD":
                raise QuoteServiceError(
                    "amount_required",
                    f"La op no tiene lado USD claro ({from_symbol}); especifica el monto a debitar",
                    400,
                )
            amount = op.from_amount
        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto a debitar debe ser > 0", 400)

        balance = self.get_balance(op.client_id)
        if balance + BALANCE_EPSILON < amount:
            raise QuoteServiceError(
                "insufficient_balance",
                f"Saldo insuficiente: {balance:.2f} USD disponibles, abono de {amount:.2f} USD",
                409,
            )

        entry = WhatsAppBalanceEntry(
            client_id=op.client_id,
            entry_type=WhatsAppBalanceEntryType.DEBIT,
            amount=amount,
            currency="USD",
            whatsapp_operation_id=op.id,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        d = entry.dict()
        d["balance_after"] = self.get_balance(op.client_id)
        return d

    # ---------- Ajuste manual ----------

    def adjust(
        self,
        client_uuid: UUID,
        entry_type: str,
        amount: float,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        """Crédito/débito manual (correcciones del operador desde el front)."""
        client = self._client_by_uuid(client_uuid)
        try:
            etype = WhatsAppBalanceEntryType(entry_type)
        except ValueError:
            raise QuoteServiceError("invalid_entry_type", f"entry_type inválido: {entry_type}", 400)
        if amount is None or amount <= 0:
            raise QuoteServiceError("invalid_amount", "El monto debe ser > 0", 400)

        entry = WhatsAppBalanceEntry(
            client_id=client.id,
            entry_type=etype,
            amount=amount,
            currency="USD",
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        d = entry.dict()
        d["balance_after"] = self.get_balance(client.id)
        return d
