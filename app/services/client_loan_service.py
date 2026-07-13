"""Contabilidad de préstamos a clientes originados en pagos salientes."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session, joinedload

from app.models.client_loan import (
    ClientLoan,
    ClientLoanPreferredValue,
    ClientLoanRepayment,
    ClientLoanStatus,
)
from app.models.bcv_rate import BcvRate
from app.models.exchange_rate import ExchangeRate
from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_payment import WhatsAppOutgoingPayment
from app.services.bcv_service import get_cached_bcv_rate
from app.services.whatsapp_quote_service import QuoteServiceError
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver


LOAN_EPSILON = 0.00000001


def _decimal(value: float) -> Decimal:
    return Decimal(str(round(float(value), 8)))


class ClientLoanService:
    def __init__(self, db: Session):
        self.db = db
        self.resolver = WhatsAppRateResolver(db)

    def _convert(self, amount: float, from_currency: str, to_currency: str) -> tuple[float, float]:
        """Devuelve (monto convertido, unidades destino por unidad origen), sin margen comercial."""
        source = from_currency.upper()
        target = to_currency.upper()
        if float(amount) == 0:
            return 0.0, 0.0
        if source == target or {source, target} == {"USD", "USDT"}:
            return float(amount), 1.0
        entry = self.resolver.get_rate_entry_for_pair(source, target)
        if entry is None or entry.base_rate <= 0:
            raise QuoteServiceError(
                "loan_rate_unavailable",
                f"No hay una tasa disponible para convertir {source} a {target}",
                409,
            )
        converted = self.resolver.apply_rate(float(amount), entry.base_rate, entry.inverse_percentage)
        return float(converted), float(converted) / float(amount)

    def _bcv_values(self, fiat_amount: float, fiat_currency: str) -> tuple[Optional[float], Optional[float]]:
        if fiat_currency.upper() != "VES":
            return None, None
        rate = get_cached_bcv_rate(self.db)
        if rate is None or rate <= 0:
            raise QuoteServiceError(
                "bcv_rate_unavailable",
                "No hay una tasa BCV disponible para valorar el préstamo",
                409,
            )
        return float(fiat_amount) / float(rate), float(rate)

    @staticmethod
    def _settlement_currency(symbol: Optional[str]) -> str:
        value = (symbol or "").upper()
        return {"ZELLE": "USD", "PAYPAL": "USD"}.get(value, value)

    def _historical_rate(
        self,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> tuple[Optional[float], Optional[bool], Optional[datetime]]:
        """Tasa base vigente inmediatamente antes del comprobante, sin margen comercial."""
        source = from_currency.upper()
        target = to_currency.upper()
        if source == target or {source, target} == {"USD", "USDT"}:
            return 1.0, False, at

        direct = (
            self.db.query(ExchangeRate)
            .filter(
                ExchangeRate.from_currency == source,
                ExchangeRate.to_currency == target,
                ExchangeRate.created_at <= at,
            )
            .order_by(ExchangeRate.created_at.desc())
            .first()
        )
        if direct is not None and direct.base_rate > 0:
            return float(direct.base_rate), bool(direct.inverse_percentage), direct.created_at

        inverse = (
            self.db.query(ExchangeRate)
            .filter(
                ExchangeRate.from_currency == target,
                ExchangeRate.to_currency == source,
                ExchangeRate.created_at <= at,
            )
            .order_by(ExchangeRate.created_at.desc())
            .first()
        )
        if inverse is None or inverse.base_rate <= 0:
            return None, None, None
        # Para recorrer el par en sentido contrario se conserva la tasa y se
        # intercambia multiplicar/dividir. Invertir también el número aplicaría
        # la inversión dos veces.
        return float(inverse.base_rate), not bool(inverse.inverse_percentage), inverse.created_at

    def _historical_convert(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
        at: datetime,
    ) -> tuple[Optional[float], Optional[datetime]]:
        rate, inverse_percentage, rate_at = self._historical_rate(from_currency, to_currency, at)
        if rate is None or inverse_percentage is None:
            return None, None
        converted = self.resolver.apply_rate(float(amount), rate, inverse_percentage)
        return float(converted), rate_at

    def _historical_bcv(self, at: datetime) -> tuple[Optional[float], Optional[datetime]]:
        row = (
            self.db.query(BcvRate)
            .filter(BcvRate.fetched_at <= at)
            .order_by(BcvRate.fetched_at.desc())
            .first()
        )
        if row is None or row.rate <= 0:
            return None, None
        return float(row.rate), row.fetched_at

    def preview_outgoing(self, payment_id: int, fiat_currency: Optional[str] = None) -> dict:
        payment = (
            self.db.query(WhatsAppOutgoingPayment)
            .filter(WhatsAppOutgoingPayment.id == payment_id)
            .first()
        )
        if payment is None:
            raise QuoteServiceError("not_found", f"Pago outgoing/{payment_id} no encontrado", 404)
        if payment.amount is None or payment.amount <= 0:
            raise QuoteServiceError("invalid_amount", "El comprobante no tiene un monto válido", 409)

        detected_currency = self._settlement_currency(payment.currency)
        valuation_at = payment.created_at or datetime.now(timezone.utc)
        target_fiat = (fiat_currency or ("VES" if detected_currency == "USDT" else detected_currency)).upper()
        warnings: list[str] = []

        if detected_currency == "USDT":
            usdt_amount: Optional[float] = float(payment.amount)
            fiat_amount, usdt_rate_at = self._historical_convert(
                usdt_amount, "USDT", target_fiat, valuation_at
            )
        else:
            target_fiat = detected_currency
            fiat_amount = float(payment.amount)
            usdt_amount, usdt_rate_at = self._historical_convert(
                fiat_amount, target_fiat, "USDT", valuation_at
            )

        usdt_rate = (
            float(fiat_amount) / float(usdt_amount)
            if fiat_amount is not None and usdt_amount is not None and usdt_amount > 0
            else None
        )
        if usdt_amount is None:
            warnings.append(f"No se encontró una tasa histórica {target_fiat}/USDT")

        bcv_amount: Optional[float] = None
        bcv_rate: Optional[float] = None
        bcv_rate_at: Optional[datetime] = None
        if target_fiat == "VES" and fiat_amount is not None:
            bcv_rate, bcv_rate_at = self._historical_bcv(valuation_at)
            if bcv_rate is None:
                warnings.append("No se encontró una tasa BCV anterior al comprobante")
            else:
                bcv_amount = float(fiat_amount) / bcv_rate

        return {
            "payment_id": payment.id,
            "detected_amount": float(payment.amount),
            "detected_currency": payment.currency,
            "fiat_amount": fiat_amount,
            "fiat_currency": target_fiat,
            "usdt_amount": usdt_amount,
            "usdt_rate": usdt_rate,
            "usdt_rate_at": usdt_rate_at,
            "bcv_amount": bcv_amount,
            "bcv_rate": bcv_rate,
            "bcv_rate_at": bcv_rate_at,
            "valuation_at": valuation_at,
            "warnings": warnings,
        }

    def _loan_by_uuid(self, loan_uuid: UUID) -> ClientLoan:
        loan = (
            self.db.query(ClientLoan)
            .options(joinedload(ClientLoan.repayments), joinedload(ClientLoan.created_by))
            .filter(ClientLoan.uuid == str(loan_uuid))
            .first()
        )
        if loan is None:
            raise QuoteServiceError("loan_not_found", "Préstamo no encontrado", 404)
        return loan

    def create_from_outgoing(
        self,
        payment_id: int,
        preferred_value: str,
        fiat_currency: str,
        fiat_amount: Optional[float] = None,
        usdt_amount: Optional[float] = None,
        bcv_amount: Optional[float] = None,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        payment = (
            self.db.query(WhatsAppOutgoingPayment)
            .filter(WhatsAppOutgoingPayment.id == payment_id)
            .first()
        )
        if payment is None:
            raise QuoteServiceError("not_found", f"Pago outgoing/{payment_id} no encontrado", 404)
        if payment.client_phone.endswith("@g.us"):
            raise QuoteServiceError("invalid_client", "No se puede registrar un préstamo a un grupo", 400)
        if payment.whatsapp_operation_id is not None:
            raise QuoteServiceError(
                "payment_has_operation",
                "Desvincula el pago de la operación antes de marcarlo como préstamo",
                409,
            )
        if payment.is_personal_expense or payment.is_irrelevant:
            raise QuoteServiceError(
                "payment_already_classified",
                "Quita la clasificación actual antes de marcar el pago como préstamo",
                409,
            )
        existing = self.db.query(ClientLoan).filter(ClientLoan.outgoing_payment_id == payment.id).first()
        if existing is not None:
            raise QuoteServiceError("loan_already_exists", "Este pago ya está registrado como préstamo", 409)

        try:
            preferred = ClientLoanPreferredValue(preferred_value.upper())
        except ValueError:
            raise QuoteServiceError("invalid_preferred_value", "Referencia preferida inválida", 400)

        fiat_currency = fiat_currency.strip().upper()
        if not fiat_currency or fiat_currency == "USDT":
            raise QuoteServiceError("invalid_fiat_currency", "Selecciona una moneda fiat válida", 400)
        if preferred == ClientLoanPreferredValue.BCV and fiat_currency != "VES":
            raise QuoteServiceError(
                "bcv_requires_ves",
                "La referencia BCV solo está disponible cuando la moneda fiat es VES",
                400,
            )

        preview = self.preview_outgoing(payment_id, fiat_currency)
        suggested_fiat = preview["fiat_amount"]
        suggested_usdt = preview["usdt_amount"]
        suggested_bcv = preview["bcv_amount"]
        fiat_amount = fiat_amount if fiat_amount is not None else suggested_fiat
        usdt_amount = usdt_amount if usdt_amount is not None else suggested_usdt
        bcv_amount = bcv_amount if bcv_amount is not None else suggested_bcv
        if fiat_currency != "VES":
            bcv_amount = None

        if fiat_amount is None or fiat_amount <= 0:
            raise QuoteServiceError("invalid_fiat_amount", "Indica un valor fiat válido", 400)
        if usdt_amount is None or usdt_amount <= 0:
            raise QuoteServiceError("invalid_usdt_amount", "Indica un valor USDT válido", 400)
        if fiat_currency == "VES" and (bcv_amount is None or bcv_amount <= 0):
            raise QuoteServiceError("invalid_bcv_amount", "Indica un valor BCV válido", 400)

        usdt_rate = float(fiat_amount) / float(usdt_amount)
        bcv_rate = (
            float(fiat_amount) / float(bcv_amount)
            if fiat_currency == "VES" and bcv_amount is not None
            else None
        )

        def changed(provided: Optional[float], suggested: Optional[float]) -> bool:
            if provided is None:
                return False
            if suggested is None:
                return True
            return abs(float(provided) - float(suggested)) > max(abs(float(suggested)) * 0.000001, 0.000001)

        manual_values = any(
            (
                changed(fiat_amount, suggested_fiat),
                changed(usdt_amount, suggested_usdt),
                changed(bcv_amount, suggested_bcv),
            )
        )

        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.phone == payment.client_phone).first()
        if client is None:
            raise QuoteServiceError("client_not_found", "El pago no tiene un cliente válido", 404)

        loan = ClientLoan(
            client_id=client.id,
            outgoing_payment_id=payment.id,
            fiat_amount=_decimal(fiat_amount),
            fiat_currency=fiat_currency,
            usdt_amount=_decimal(usdt_amount),
            usdt_rate=_decimal(usdt_rate),
            bcv_amount=_decimal(bcv_amount) if bcv_amount is not None else None,
            bcv_rate=_decimal(bcv_rate) if bcv_rate is not None else None,
            valuation_at=preview["valuation_at"],
            manual_values=manual_values,
            preferred_value=preferred,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(loan)
        self.db.commit()
        self.db.refresh(loan)
        return self.serialize(loan)

    def _current_fiat_due(self, loan: ClientLoan) -> tuple[Optional[float], Optional[float]]:
        outstanding = loan.outstanding_amount
        if loan.preferred_value == ClientLoanPreferredValue.FIAT:
            return outstanding, 1.0
        if outstanding <= LOAN_EPSILON:
            return 0.0, 0.0
        try:
            if loan.preferred_value == ClientLoanPreferredValue.USDT:
                fiat, _ = self._convert(outstanding, "USDT", loan.fiat_currency)
                return fiat, fiat / outstanding if outstanding > 0 else 0.0
            rate = get_cached_bcv_rate(self.db)
            if rate is None or rate <= 0:
                return None, None
            return outstanding * float(rate), float(rate)
        except QuoteServiceError:
            return None, None

    def serialize(self, loan: ClientLoan) -> dict:
        current_fiat_due, current_preferred_rate = self._current_fiat_due(loan)
        return {
            "uuid": loan.uuid,
            "client_uuid": loan.client.uuid if loan.client else None,
            "outgoing_payment_id": loan.outgoing_payment_id,
            "fiat_amount": float(loan.fiat_amount),
            "fiat_currency": loan.fiat_currency,
            "usdt_amount": float(loan.usdt_amount),
            "usdt_rate": float(loan.usdt_rate),
            "bcv_amount": float(loan.bcv_amount) if loan.bcv_amount is not None else None,
            "bcv_rate": float(loan.bcv_rate) if loan.bcv_rate is not None else None,
            "valuation_at": loan.valuation_at,
            "manual_values": bool(loan.manual_values),
            "preferred_value": loan.preferred_value.value,
            "preferred_currency": loan.preferred_currency,
            "principal_amount": loan.preferred_principal,
            "outstanding_amount": loan.outstanding_amount,
            "current_fiat_due": current_fiat_due,
            "current_preferred_rate": current_preferred_rate,
            "status": loan.status.value,
            "notes": loan.notes,
            "created_by_username": loan.created_by.username if loan.created_by else None,
            "created_at": loan.created_at,
            "updated_at": loan.updated_at,
            "repayments": [entry.dict() for entry in loan.repayments],
        }

    def list_for_client(self, client_uuid: UUID) -> dict:
        client = self.db.query(WhatsAppClient).filter(WhatsAppClient.uuid == str(client_uuid)).first()
        if client is None:
            raise QuoteServiceError("client_not_found", "Cliente no encontrado", 404)
        loans = (
            self.db.query(ClientLoan)
            .options(joinedload(ClientLoan.repayments), joinedload(ClientLoan.created_by))
            .filter(ClientLoan.client_id == client.id)
            .order_by(ClientLoan.created_at.desc())
            .all()
        )
        return {"client_uuid": client.uuid, "loans": [self.serialize(loan) for loan in loans]}

    def add_repayment(
        self,
        loan_uuid: UUID,
        preferred_amount: float,
        notes: Optional[str] = None,
        created_by_user_id: Optional[int] = None,
    ) -> dict:
        loan = self._loan_by_uuid(loan_uuid)
        if loan.status in (ClientLoanStatus.PAID, ClientLoanStatus.CANCELLED):
            raise QuoteServiceError("loan_closed", "El préstamo ya está cerrado", 409)
        if preferred_amount <= 0:
            raise QuoteServiceError("invalid_amount", "El abono debe ser mayor a 0", 400)
        outstanding = loan.outstanding_amount
        if preferred_amount - outstanding > LOAN_EPSILON:
            raise QuoteServiceError(
                "repayment_exceeds_balance",
                f"El abono supera el saldo pendiente de {outstanding:.8f} {loan.preferred_currency}",
                409,
            )

        if loan.preferred_value == ClientLoanPreferredValue.FIAT:
            fiat_amount = preferred_amount
        elif loan.preferred_value == ClientLoanPreferredValue.USDT:
            fiat_amount, _ = self._convert(preferred_amount, "USDT", loan.fiat_currency)
        else:
            bcv_rate_now = get_cached_bcv_rate(self.db)
            if bcv_rate_now is None or bcv_rate_now <= 0:
                raise QuoteServiceError("bcv_rate_unavailable", "No hay una tasa BCV disponible", 409)
            fiat_amount = preferred_amount * float(bcv_rate_now)

        usdt_amount, _ = self._convert(fiat_amount, loan.fiat_currency, "USDT")
        usdt_rate = fiat_amount / usdt_amount
        bcv_amount, bcv_rate = self._bcv_values(fiat_amount, loan.fiat_currency)

        repayment = ClientLoanRepayment(
            loan_id=loan.id,
            preferred_amount=_decimal(preferred_amount),
            fiat_amount=_decimal(fiat_amount),
            fiat_currency=loan.fiat_currency,
            usdt_amount=_decimal(usdt_amount),
            usdt_rate=_decimal(usdt_rate),
            bcv_amount=_decimal(bcv_amount) if bcv_amount is not None else None,
            bcv_rate=_decimal(bcv_rate) if bcv_rate is not None else None,
            notes=notes,
            created_by_user_id=created_by_user_id,
        )
        self.db.add(repayment)
        paid_after = outstanding - preferred_amount
        loan.status = ClientLoanStatus.PAID if paid_after <= LOAN_EPSILON else ClientLoanStatus.PARTIAL
        self.db.commit()
        self.db.refresh(loan)
        return self.serialize(loan)
