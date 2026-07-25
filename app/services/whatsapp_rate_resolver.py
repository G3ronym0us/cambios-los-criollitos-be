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
from datetime import datetime
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

    def get_rate_entry_for_pair(
        self, from_currency: str, to_currency: str, at: Optional[datetime] = None
    ) -> Optional[RateEntry]:
        """
        Tasa del par. Sin `at` es la vigente; con `at`, la que regía en ese momento
        (para leer una operación vieja con la tasa de su día, no con la de hoy).
        """
        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency == to_currency:
            return RateEntry(rate=1.0, inverse_percentage=False, base_percentage=None, base_rate=1.0)

        direct = self._get_direct_entry(from_currency, to_currency, at)
        if direct is not None:
            return direct

        # Cross via USDT bridge
        if from_currency != "USDT" and to_currency != "USDT":
            leg1 = self._get_direct_entry(from_currency, "USDT", at)
            leg2 = self._get_direct_entry("USDT", to_currency, at)
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

    @classmethod
    def implied_margin(cls, entry: RateEntry, effective_rate: float) -> Optional[float]:
        """
        La contracara de `rate_with_margin`: qué margen hay dentro de una tasa ya usada.

        `effective_rate` va en unidades `to` por unidad de `from` (multiplicar), que es como
        queda `to_amount / from_amount`. Se compara contra la tasa base del par en esas mismas
        unidades, así que sirve igual para pares directos que inversos.

        Devuelve None cuando no hay margen que leer —el par no lo define (BCV, manual, cruce
        por USDT)— o cuando el resultado se sale del rango de un margen comercial: ahí la tasa
        no salió de este par y afirmar una ganancia sería inventarla.
        """
        if entry is None or entry.base_percentage is None:
            return None
        if not effective_rate or effective_rate <= 0:
            return None
        base = cls.apply_rate(1.0, entry.base_rate, entry.inverse_percentage)
        if base <= 0:
            return None
        # Redondeo: sin él, una tasa reconstruida del par devuelve 7.999999999999 en vez de 8.
        margin = round((1 - effective_rate / base) * 100, 4)
        if margin == 0:
            return 0.0  # cotizada a la tasa base: sin margen, pero no `-0.0`
        return margin if 0 < margin < 99 else None

    # ---------- Lookups internos ----------

    def _get_direct_entry(
        self, from_currency: str, to_currency: str, at: Optional[datetime] = None
    ) -> Optional[RateEntry]:
        # 1. Par directo (incluye override manual si existe — está reflejado en `rate`)
        direct = self._fetch_latest_active(from_currency, to_currency, at)
        if direct is not None:
            return self._to_entry(direct)

        # 2. Par inverso: swap y se invierte la tasa
        inverse = self._fetch_latest_active(to_currency, from_currency, at)
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

    def _fetch_latest_active(
        self, from_currency: str, to_currency: str, at: Optional[datetime] = None
    ) -> Optional[ExchangeRate]:
        query = self.db.query(ExchangeRate).filter(
            ExchangeRate.from_currency == from_currency,
            ExchangeRate.to_currency == to_currency,
        )
        if at is None:
            query = query.filter(ExchangeRate.is_active == True)
        else:
            # La tasa que regía entonces hoy ya no está activa: la desactivó la siguiente.
            query = query.filter(ExchangeRate.created_at <= at)
        return query.order_by(ExchangeRate.created_at.desc()).first()

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
