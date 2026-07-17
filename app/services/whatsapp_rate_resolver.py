"""
Resolución de tasas para cotizaciones del bot de WhatsApp.

Port directo de la lógica en whatsapp-bot/src/rates.ts y whatsapp-bot/src/calculator.ts:
  - getRateEntryForPair(): directo → inverso → cross via USDT bridge
  - applyRate(): aplica tasa según flag inverse_percentage
  - rateWithMargin(): recalcula tasa con un margen distinto al base

Mantener paridad EXACTA con el bot durante la coexistencia; los tests en
backend/tests/test_whatsapp_rate_resolver.py validan contra los casos de
whatsapp-bot/src/test-cases/routing.json.
"""

import math
from dataclasses import dataclass
from typing import Optional
from sqlalchemy.orm import Session

from app.models.exchange_rate import ExchangeRate


def apply_rounding(amount: float, step: float, direction: str) -> float:
    """Redondea `amount` al múltiplo `step` en la dirección indicada ('UP'/'DOWN').

    Con `step` inválido o dirección desconocida devuelve el monto sin tocar.
    Usa un epsilon para que valores ya-múltiplos (afectados por error de float)
    no salten al siguiente escalón.
    """
    if not step or step <= 0:
        return amount
    q = amount / step
    eps = 1e-9
    if direction == "UP":
        return math.ceil(q - eps) * step
    if direction == "DOWN":
        return math.floor(q + eps) * step
    return amount


@dataclass
class RateEntry:
    rate: float
    inverse_percentage: bool
    base_percentage: Optional[float]  # margen base que aplica la fuente; None si manual u override
    base_rate: float                   # tasa cruda sin margen


class WhatsAppRateResolver:
    """
    Resolver sin estado interno; cada instancia toma una Session.
    Los lookups se hacen contra `exchange_rates` (is_active=True) directamente.
    """

    def __init__(self, db: Session):
        self.db = db

    # ---------- API pública ----------

    def get_rate_entry_for_pair(self, from_currency: str, to_currency: str) -> Optional[RateEntry]:
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return RateEntry(rate=1.0, inverse_percentage=False, base_percentage=None, base_rate=1.0)

        direct = self._get_direct_entry(from_currency, to_currency)
        if direct is not None:
            return direct

        # Cross via USDT bridge
        if from_currency != "USDT" and to_currency != "USDT":
            leg1 = self._get_direct_entry(from_currency, "USDT")
            leg2 = self._get_direct_entry("USDT", to_currency)
            if leg1 is not None and leg2 is not None:
                intermediate = self.apply_rate(1.0, leg1.rate, leg1.inverse_percentage)
                combined = self.apply_rate(intermediate, leg2.rate, leg2.inverse_percentage)
                return RateEntry(
                    rate=combined,
                    inverse_percentage=False,
                    base_percentage=None,
                    base_rate=combined,
                )

        return None

    @staticmethod
    def apply_rate(amount: float, rate: float, inverse_percentage: bool) -> float:
        return amount / rate if inverse_percentage else amount * rate

    @staticmethod
    def rate_with_margin(base_rate: float, margin_pct: float, inverse_percentage: bool) -> Optional[float]:
        factor = 1 - margin_pct / 100
        if factor <= 0:
            return None
        return base_rate / factor if inverse_percentage else base_rate * factor

    # ---------- Lookups internos ----------

    def _get_direct_entry(self, from_currency: str, to_currency: str) -> Optional[RateEntry]:
        # 1. Par directo (incluye override manual si existe — está reflejado en `rate`)
        direct = self._fetch_latest_active(from_currency, to_currency)
        if direct is not None:
            return self._to_entry(direct)

        # 2. Par inverso: swap y se invierte la tasa
        inverse = self._fetch_latest_active(to_currency, from_currency)
        if inverse is not None and inverse.rate != 0:
            base_inv = self._to_entry(inverse)
            inverted_base = (1.0 / base_inv.base_rate) if base_inv.base_rate != 0 else 0.0
            return RateEntry(
                rate=1.0 / inverse.rate,
                inverse_percentage=not base_inv.inverse_percentage,
                base_percentage=base_inv.base_percentage,
                base_rate=inverted_base,
            )

        return None

    def _fetch_latest_active(self, from_currency: str, to_currency: str) -> Optional[ExchangeRate]:
        return (
            self.db.query(ExchangeRate)
            .filter(
                ExchangeRate.from_currency == from_currency,
                ExchangeRate.to_currency == to_currency,
                ExchangeRate.is_active == True,
            )
            .order_by(ExchangeRate.created_at.desc())
            .first()
        )

    @staticmethod
    def _to_entry(er: ExchangeRate) -> RateEntry:
        """
        Convierte un ExchangeRate de Postgres a la abstracción RateEntry.
        Reusa la propiedad `base_rate` del modelo (ExchangeRate.base_rate),
        que ya considera el margen aplicado y si es manual.
        """
        return RateEntry(
            rate=float(er.rate),
            inverse_percentage=bool(er.inverse_percentage),
            base_percentage=float(er.percentage) if er.percentage is not None else None,
            base_rate=float(er.base_rate),
        )
