"""
La bandera `settles_in_cash` de un par, de punta a punta.

Es la única manera de encender el caso de efectivo: sin ella, «por entregar» exige un
comprobante entrante que en un par mano a mano no existe, y el par entero desaparece de la
pantalla. Como no hay control en el panel todavía, el camino real es la API — así que lo que
importa es que el campo sobreviva al crear, al editar y al responder.
"""

import pytest

from app.enums.pair_type import PairType
from app.models.currency import Currency
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.schemas.currency_pair import (
    CurrencyPairCreate,
    CurrencyPairResponse,
    CurrencyPairUpdate,
)


def _currency(db, symbol: str) -> Currency:
    row = db.query(Currency).filter(Currency.symbol == symbol).first()
    if row is None:
        row = Currency(symbol=symbol, name=symbol)
        db.add(row)
        db.flush()
    return row


async def _pair(db, frm: str, to: str, **kwargs):
    return await CurrencyPairRepository(db).create_currency_pair(
        CurrencyPairCreate(
            from_currency_uuid=_currency(db, frm).uuid,
            to_currency_uuid=_currency(db, to).uuid,
            pair_type=PairType.BASE,
            is_monitored=False,
            **kwargs,
        )
    )


@pytest.mark.asyncio
async def test_un_par_normal_no_es_de_efectivo(db):
    """El valor por defecto importa: encenderlo sin querer relajaría la regla de la deuda."""
    pair = await _pair(db, "ZELLE", "VES")
    assert pair.settles_in_cash is False


@pytest.mark.asyncio
async def test_se_puede_crear_ya_marcado(db):
    pair = await _pair(db, "USD", "VES", settles_in_cash=True)
    assert pair.settles_in_cash is True


@pytest.mark.asyncio
async def test_se_puede_encender_y_apagar_despues(db):
    """Un par ya existente —el USD-VES de producción— se marca editándolo, no recreándolo."""
    repo = CurrencyPairRepository(db)
    pair = await _pair(db, "USD", "COP")

    encendido = await repo.update_currency_pair(pair.id, CurrencyPairUpdate(settles_in_cash=True))
    assert encendido.settles_in_cash is True

    apagado = await repo.update_currency_pair(pair.id, CurrencyPairUpdate(settles_in_cash=False))
    assert apagado.settles_in_cash is False


@pytest.mark.asyncio
async def test_editar_otra_cosa_no_apaga_la_bandera(db):
    """`exclude_unset`: tocar la descripción no puede devolver el par a «no es de efectivo»."""
    repo = CurrencyPairRepository(db)
    pair = await _pair(db, "USD", "BRL", settles_in_cash=True)

    editado = await repo.update_currency_pair(
        pair.id, CurrencyPairUpdate(description="Efectivo mano a mano")
    )
    assert editado.settles_in_cash is True


@pytest.mark.asyncio
async def test_la_respuesta_lo_dice(db):
    """Es lo que lee el panel de pares para saber cuáles están en efectivo."""
    pair = await _pair(db, "USD", "PEN", settles_in_cash=True)
    assert CurrencyPairResponse(**pair.dict()).settles_in_cash is True
