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


def test_la_operacion_guarda_el_fondo_que_paga(db, fund, pairs, client, operator):
    """La pata que sale tiene su propio fondo, y sale en el dict de la op."""
    from datetime import timedelta

    from app.models.whatsapp_operation import (
        WhatsAppAmountSide, WhatsAppOperation, WhatsAppOperationStatus,
    )

    brasil = FundGroup(name="Cambios Brasil test", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-BRL"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency="ZELLE",
        fund_group_id=fund.id, fund_group_out_id=brasil.id,
        created_at=now, quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()

    assert op.fund_group_out.name == "Cambios Brasil test"
    assert op.dict()["fund_group_out_uuid"] == brasil.uuid
    assert op.dict()["fund_group_out_name"] == "Cambios Brasil test"


def test_el_fondo_de_una_moneda_se_resuelve_solo(db, fund):
    """ZELLE liquida como USD, así que cae en el fondo USD."""
    from app.services.valuation import settlement_currency

    repo = FundRepository(db)

    assert repo.get_active_group_by_currency(settlement_currency("ZELLE")).id == fund.id
    assert repo.get_active_group_by_currency("USD").id == fund.id


def test_una_moneda_sin_fondo_no_resuelve(db, fund):
    """Los bolívares no tienen fondo: esa pata no deja movimiento."""
    assert FundRepository(db).get_active_group_by_currency("VES") is None


def test_dos_fondos_de_la_misma_moneda_no_resuelven(db, fund):
    """Ante dos candidatos el sistema no adivina: lo elige el operador."""
    otro = FundGroup(name="Otro USD", currency="USD", is_active=True)
    db.add(otro)
    db.flush()

    assert FundRepository(db).get_active_group_by_currency("USD") is None


def test_un_fondo_inactivo_no_compite(db, fund):
    """Un fondo desactivado no cuenta ni para resolver ni para ambiguar."""
    viejo = FundGroup(name="USD viejo", currency="USD", is_active=False)
    db.add(viejo)
    db.flush()

    assert FundRepository(db).get_active_group_by_currency("USD").id == fund.id
