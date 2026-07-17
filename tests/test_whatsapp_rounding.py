"""
Tests del redondeo configurable de cotizaciones (por par de monedas).

`apply_rounding` es una función pura y `_apply_pair_rounding` solo usa
`resolver.apply_rate` (estático), así que ninguno necesita base de datos: se
prueban con pares/monedas falsos, replicando la decisión que toma `create_quote`.
"""

import pytest

from app.services.whatsapp_rate_resolver import WhatsAppRateResolver, apply_rounding
from app.services.whatsapp_quote_service import WhatsAppQuoteService


# ---------- apply_rounding (función pura) ----------

@pytest.mark.parametrize("amount,step,direction,expected", [
    (49741, 100, "UP", 49800),
    (49741, 100, "DOWN", 49700),
    (798, 5, "UP", 800),
    (798, 5, "DOWN", 795),
    (800, 5, "UP", 800),      # ya-múltiplo no salta de escalón
    (11000, 5, "UP", 11000),
    (753, 5, "UP", 755),
])
def test_apply_rounding(amount, step, direction, expected):
    assert apply_rounding(amount, step, direction) == expected


def test_apply_rounding_invalid_step_is_noop():
    assert apply_rounding(123.45, 0, "UP") == 123.45
    assert apply_rounding(123.45, None, "UP") == 123.45


# ---------- _apply_pair_rounding (decisión de create_quote) ----------

class _Cur:
    def __init__(self, symbol):
        self.symbol = symbol


class _Pair:
    def __init__(self, mode=None, step=None, direction=None, side=None, fc="COP", tc="VES"):
        self.rounding_mode = mode
        self.rounding_step = step
        self.rounding_direction = direction
        self.rounding_amount_side = side
        self.from_currency = _Cur(fc)
        self.to_currency = _Cur(tc)


@pytest.fixture
def svc():
    s = object.__new__(WhatsAppQuoteService)

    class _R:
        apply_rate = staticmethod(WhatsAppRateResolver.apply_rate)

    s.resolver = _R()
    return s


def test_amount_mode_rounds_calculated_from_side(svc):
    """COP-VES, AMOUNT/100/UP/FROM: cliente pide 11000 Bs → COP calculado se redondea."""
    pair = _Pair(mode="AMOUNT", step=100, direction="UP", side="FROM", fc="COP", tc="VES")
    rate = 49741 / 11000  # COP por VES
    from_a, to_a, r, inv = svc._apply_pair_rounding(
        pair, "COP", "VES", "RECEIVE", 49741.0, 11000.0, rate, False
    )
    assert from_a == 49800          # redondeado hacia arriba
    assert to_a == 11000            # lado fijado por el cliente, intacto
    assert (r, inv) == (rate, False)  # la tasa no cambia en modo AMOUNT


def test_amount_mode_noop_when_target_is_client_input(svc):
    """Mismo par: si el cliente ENVÍA 50000 COP (COP es input), el Bs queda exacto."""
    pair = _Pair(mode="AMOUNT", step=100, direction="UP", side="FROM", fc="COP", tc="VES")
    rate = 49741 / 11000
    to_calc = 50000 / rate
    from_a, to_a, _, _ = svc._apply_pair_rounding(
        pair, "COP", "VES", "SEND", 50000.0, to_calc, rate, False
    )
    assert from_a == 50000          # input intacto
    assert to_a == pytest.approx(to_calc)  # sin redondear


def test_rate_mode_rounds_effective_rate_then_multiplies(svc):
    """USD-VES, RATE/5/UP: tasa 798 → 800; $15 → 12000 (no 15*798 redondeado)."""
    pair = _Pair(mode="RATE", step=5, direction="UP", fc="USD", tc="VES")
    from_a, to_a, r, inv = svc._apply_pair_rounding(
        pair, "USD", "VES", "SEND", 15.0, 15 * 798.0, 798.0, False
    )
    assert to_a == 12000
    assert r == 800
    assert inv is False


def test_rate_mode_receive_side(svc):
    """RATE/5/UP en RECEIVE: cliente quiere 12000 Bs a tasa redondeada 800 → paga $15."""
    pair = _Pair(mode="RATE", step=5, direction="UP", fc="USD", tc="VES")
    from_a, to_a, r, _ = svc._apply_pair_rounding(
        pair, "USD", "VES", "RECEIVE", 12000 / 798.0, 12000.0, 798.0, False
    )
    assert from_a == pytest.approx(15.0)
    assert r == 800


def test_no_config_is_noop(svc):
    pair = _Pair()  # sin rounding_mode
    result = svc._apply_pair_rounding(pair, "USD", "VES", "SEND", 15.0, 11970.0, 798.0, False)
    assert result == (15.0, 11970.0, 798.0, False)
