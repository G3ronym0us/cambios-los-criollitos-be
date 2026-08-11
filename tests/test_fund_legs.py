"""Las dos patas de una operación en el libro de fondos."""

from datetime import datetime, timezone

import pytest

from app.models.fund import FundGroup, FundGroupMember, FundMovement, FundMovementType
from app.repositories.fund_repository import FundRepository


def _mov(db, group, user, mtype, amount, currency, usdt):
    row = FundMovement(
        group_id=group.id, user_id=user.id, movement_type=mtype,
        amount=amount, currency=currency, amount_usdt=usdt,
        movement_date=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def test_exchange_in_suma_donde_exchange_resta(db, fund, operator):
    """La pata que entra sube el fondo; la que sale lo baja."""
    entra = _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    sale = _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    saldos = FundRepository(db).get_running_totals(fund.id, [entra.id, sale.id])

    assert saldos[entra.id]["balance_usdt"] == 100
    assert saldos[sale.id]["balance_usdt"] == 60


def test_exchange_in_cuenta_como_entrada_en_la_posicion(db, fund, operator):
    """`position` se lee 'el fondo le debe al gestor': la pata que entra la aumenta."""
    _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    pos = FundRepository(db).get_user_position(operator.id, fund.id)

    assert pos["total_deposited_usdt"] == 100
    assert pos["total_outflow_usdt"] == 40
    assert pos["position_usdt"] == 60


def test_exchange_in_cuenta_como_entrada_en_el_balance_del_grupo(db, fund, operator):
    _mov(db, fund, operator, FundMovementType.EXCHANGE_IN, 100, "USD", 100)
    _mov(db, fund, operator, FundMovementType.EXCHANGE, 40, "USD", 40)

    balance = FundRepository(db).get_group_balance(fund.id)

    assert balance["total_position_usdt"] == 60
