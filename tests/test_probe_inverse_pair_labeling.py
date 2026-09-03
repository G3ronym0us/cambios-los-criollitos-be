"""
Probe (agente 1, campaña de ruptura): ¿qué pasa cuando el bot cotiza un par en la
dirección CONTRARIA a como está registrado en `currency_pairs`?

`create_quote` (whatsapp_quote_service.py:130-142) busca primero el símbolo exacto
("VES-USDT") y si no existe cae al símbolo inverso ("USDT-VES"). Cuando cae al inverso,
usa ESE CurrencyPair como `currency_pair_id` de la operación -- pero `from_amount` /
`to_amount` se calculan y guardan en la dirección real del payload (VES->USDT), no en la
del `CurrencyPair` resuelto (USDT->VES).

`WhatsAppOperation.dict()` arma `from_currency`/`to_currency` desde `cp.from_currency` /
`cp.to_currency` (siempre la dirección canónica del par), pero `from_amount`/`to_amount`
son los que de verdad se calcularon en la dirección del payload. El resultado es que la
API puede etiquetar un monto bajo la moneda que NO es.
"""

import pytest

from app.models.currency import Currency
from app.models.currency_pair import CurrencyPair
from app.models.exchange_rate import ExchangeRate
from app.models.whatsapp_client import WhatsAppClient
from app.schemas.whatsapp import WhatsAppOperationCreate
from app.services.whatsapp_quote_service import WhatsAppQuoteService


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


@pytest.fixture
def usdt_ves_pair(db) -> CurrencyPair:
    """Solo existe el par en sentido USDT->VES; VES->USDT no tiene fila propia."""
    pair = CurrencyPair(
        from_currency_id=_currency(db, "USDT").id,
        to_currency_id=_currency(db, "VES").id,
        pair_symbol="USDT-VES",
        is_active=True,
    )
    db.add(pair)
    db.flush()
    db.add(ExchangeRate(
        currency_pair_id=pair.id,
        from_currency="USDT",
        to_currency="VES",
        rate=967.0,
        is_active=True,
    ))
    db.flush()
    return pair


@pytest.fixture
def authorized_client(db) -> WhatsAppClient:
    # is_usdt_authorized=True evita el atajo BCV (no es lo que se está probando acá).
    c = WhatsAppClient(phone="584120000000", display_name="Prueba", is_usdt_authorized=True)
    db.add(c)
    db.flush()
    return c


@pytest.mark.xfail(
    strict=True,
    reason=(
        "H-1.1 (agente 1, campaña de ruptura, 2026-09-02/03): bug real, no arreglado -- "
        "requiere auditar todos los consumidores de op.currency_pair.from_currency/"
        "to_currency (whatsapp_payment_service.py tiene ~10, incluida la resolución de "
        "fondos por moneda). Ver hallazgos/01-cotizaciones-pares.md H-1.1."
    ),
)
def test_quote_in_inverse_direction_mislabels_currencies(db, usdt_ves_pair, authorized_client):
    """
    El cliente entrega 95.700 VES y pide USDT (par sólo registrado como USDT-VES).

    Se espera que la API describa la operación como "95700 VES -> X USDT". En cambio,
    como `currency_pair_id` apunta al par canónico (USDT->VES) resuelto por el fallback
    inverso, `dict()` la describe como "95700 USDT -> X VES": la etiqueta de moneda del
    `from_amount` queda cruzada con la del `to_amount`.
    """
    svc = WhatsAppQuoteService(db)
    op = svc.create_quote(WhatsAppOperationCreate(
        client_phone=authorized_client.phone,
        from_currency="VES",
        to_currency="USDT",
        amount=95700,
        amount_side="SEND",
    ))

    data = op.dict()

    # Los montos en sí están bien calculados (mismo número que en op.currency, que sí es
    # fiel a lo que pidió el cliente).
    assert op.currency == "VES"
    assert data["from_amount"] == pytest.approx(95700)
    assert data["to_amount"] == pytest.approx(95700 / 967.0)

    # El bug: la etiqueta de moneda del from_amount debería seguir siendo la moneda real
    # que entregó el cliente (VES), no la que decida el CurrencyPair canónico resuelto.
    assert data["from_currency"] == op.currency, (
        f"dict() dice from_currency={data['from_currency']!r} con from_amount="
        f"{data['from_amount']!r}, pero el cliente entregó {op.currency!r}. Un "
        f"consumidor de la API lee '95700 {data['from_currency']}' cuando en realidad "
        f"fueron 95700 {op.currency} (~967x menos valor)."
    )
