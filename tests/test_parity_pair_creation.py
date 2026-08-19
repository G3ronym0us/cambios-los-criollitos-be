"""
Crear un par de paridad: la misma moneda a los dos lados.

`USDT-USDT` con precio manual 1 es la base de la que cuelga `ZELLE-USDT` al 7 %, y sin ella
no hay forma de expresar «un porcentaje sobre la paridad» en un modelo donde todo porcentaje
necesita un par base. El schema lo rechazaba con «From and to currencies must be different»,
así que esa configuración no se podía crear ni por la API ni desde el panel.

La regla venía de suponer que un par siempre cruza dos monedas distintas. Un par de paridad
no se cotiza —el resolver corta en seco cuando `from == to` y devuelve 1— así que no abre
ninguna cotización rara: solo existe para que otros pares cuelguen de él.
"""

import pytest

from app.enums.pair_type import PairType
from app.models.currency import Currency
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.schemas.currency_pair import CurrencyPairCreate


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


@pytest.mark.asyncio
async def test_parity_pair_can_be_created(db):
    """El caso USDT-USDT del par ZELLE→USDT."""
    usdt = _currency(db, "USDT")

    pair = await CurrencyPairRepository(db).create_currency_pair(
        CurrencyPairCreate(
            from_currency_uuid=usdt.uuid,
            to_currency_uuid=usdt.uuid,
            pair_type=PairType.BASE,
            description="Paridad 1:1, base de los pares de método de pago",
            is_monitored=False,
        )
    )

    assert pair.pair_symbol == "USDT-USDT"
    assert pair.from_currency_id == pair.to_currency_id


@pytest.mark.asyncio
async def test_a_normal_pair_still_works(db):
    """La ruta de siempre no cambia al quitar la validación."""
    ves = _currency(db, "VES")
    usdt = _currency(db, "USDT")

    pair = await CurrencyPairRepository(db).create_currency_pair(
        CurrencyPairCreate(
            from_currency_uuid=ves.uuid,
            to_currency_uuid=usdt.uuid,
            pair_type=PairType.BASE,
            description="Par base de bolívares",
            is_monitored=False,
        )
    )

    assert pair.pair_symbol == "VES-USDT"
