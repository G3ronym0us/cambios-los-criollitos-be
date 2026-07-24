"""
Valuación histórica: cuánto valía un monto en otra moneda en un momento dado.

Vivía dentro de `ClientLoanService` (los préstamos se guardan valorados en fiat, USDT y BCV);
las operaciones necesitan lo mismo para guardar su valor con equivalente USDT —y BCV cuando
hay bolívares—, así que se movió aquí y el servicio de préstamos delega.

Se usa la tasa BASE (sin margen comercial) vigente inmediatamente antes de la fecha pedida:
valorar es contabilidad, no cotizar.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.bcv_rate import BcvRate
from app.models.exchange_rate import ExchangeRate
from app.services.whatsapp_rate_resolver import WhatsAppRateResolver

# ZELLE y PAYPAL son métodos de pago en dólares, no monedas propias.
_SETTLEMENT = {"ZELLE": "USD", "PAYPAL": "USD"}


def settlement_currency(symbol: Optional[str]) -> str:
    return _SETTLEMENT.get((symbol or "").upper(), (symbol or "").upper())


def historical_rate(
    db: Session,
    from_currency: str,
    to_currency: str,
    at: datetime,
) -> tuple[Optional[float], Optional[bool], Optional[datetime]]:
    """Tasa base vigente inmediatamente antes de `at`, sin margen comercial."""
    source = settlement_currency(from_currency)
    target = settlement_currency(to_currency)
    if source == target or {source, target} == {"USD", "USDT"}:
        return 1.0, False, at

    direct = (
        db.query(ExchangeRate)
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
        db.query(ExchangeRate)
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
    # Para recorrer el par en sentido contrario se conserva la tasa y se intercambia
    # multiplicar/dividir. Invertir también el número aplicaría la inversión dos veces.
    return float(inverse.base_rate), not bool(inverse.inverse_percentage), inverse.created_at


def historical_convert(
    db: Session,
    amount: float,
    from_currency: str,
    to_currency: str,
    at: datetime,
) -> tuple[Optional[float], Optional[datetime]]:
    rate, inverse_percentage, rate_at = historical_rate(db, from_currency, to_currency, at)
    if rate is None or inverse_percentage is None:
        return None, None
    converted = WhatsAppRateResolver(db).apply_rate(float(amount), rate, inverse_percentage)
    return float(converted), rate_at


def historical_bcv(db: Session, at: datetime) -> tuple[Optional[float], Optional[datetime]]:
    row = (
        db.query(BcvRate)
        .filter(BcvRate.fetched_at <= at)
        .order_by(BcvRate.fetched_at.desc())
        .first()
    )
    if row is None or row.rate <= 0:
        return None, None
    return float(row.rate), row.fetched_at


def equivalents(db: Session, amount: float, currency: str, at: datetime) -> dict:
    """
    Cuánto vale `amount` en USDT —y en USD BCV si el monto está en bolívares— a fecha `at`.

    Devuelve siempre las cuatro claves; en `warnings` queda por qué falta algo, para que el
    caller decida si guarda el valor sin equivalente o corta.
    """
    warnings: list[str] = []
    source = settlement_currency(currency)
    usdt_amount, usdt_rate = None, None
    bcv_amount, bcv_rate = None, None

    if not source or amount is None or amount <= 0:
        return {
            "usdt_amount": None, "usdt_rate": None,
            "bcv_amount": None, "bcv_rate": None,
            "valuation_at": at, "warnings": ["Monto o moneda no válidos para valorar"],
        }

    usdt_amount, _ = historical_convert(db, amount, source, "USDT", at)
    if usdt_amount is None or usdt_amount <= 0:
        usdt_amount = None
        warnings.append(f"No se encontró una tasa histórica {source}/USDT")
    else:
        usdt_rate = float(amount) / float(usdt_amount)

    if source == "VES":
        rate, _ = historical_bcv(db, at)
        if rate is None:
            warnings.append("No se encontró una tasa BCV anterior a la operación")
        else:
            bcv_rate = rate
            bcv_amount = float(amount) / rate

    return {
        "usdt_amount": usdt_amount,
        "usdt_rate": usdt_rate,
        "bcv_amount": bcv_amount,
        "bcv_rate": bcv_rate,
        "valuation_at": at,
        "warnings": warnings,
    }
