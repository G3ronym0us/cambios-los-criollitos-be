"""
El fondo por defecto de una operación lo dice el par, no la moneda.

Diseño: docs/superpowers/specs/2026-09-13-fondo-por-defecto-del-par-design.md
"""

import asyncio
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.models.fund import FundGroup
from app.models.whatsapp_operation import (
    WhatsAppAmountSide,
    WhatsAppOperation,
    WhatsAppOperationStatus,
)
from app.repositories.currency_pair_repository import CurrencyPairRepository
from app.schemas.currency_pair import CurrencyPairUpdate
from app.services.profit_allocation_service import PAIR_DEFAULT_NOTE, ProfitAllocationService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f


@pytest.fixture
def brasil(db) -> FundGroup:
    group = FundGroup(name="Cambios Brasil", currency="BRL", is_active=True)
    db.add(group)
    db.flush()
    return group


def _op_from_incoming(db, client, operator, frm, to, fund_uuid=None) -> WhatsAppOperation:
    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, frm, phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm=frm, to=to, from_amount=100, to_amount=465.75,
        fund_uuid=fund_uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    return db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()


def _op(db, pair, client, fund_in, fund_out, charged) -> WhatsAppOperation:
    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pair.id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.QUOTED, amount=100, currency="ZELLE",
        amount_usdt=100, usdt_rate=1, applied_percentage=charged,
        fund_group_id=fund_in.id if fund_in else None,
        fund_group_out_id=fund_out.id if fund_out else None,
        created_at=now, quoted_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    return op


def _split_7_3(db, pairs, fund, brasil):
    pair = pairs["ZELLE-BRL"]
    pair.default_fund_in_id = fund.id
    pair.default_fund_in_profit_pct = 7
    pair.default_fund_out_id = brasil.id
    pair.default_fund_out_profit_pct = 3
    db.flush()
    return pair


# --------------------------------------------------------------- fondo de cada pata

def test_la_op_del_panel_nace_con_los_fondos_del_par(db, fund, brasil, pairs, client, operator):
    pairs["ZELLE-BRL"].default_fund_in_id = fund.id
    pairs["ZELLE-BRL"].default_fund_out_id = brasil.id
    db.flush()

    op = _op_from_incoming(db, client, operator, "ZELLE", "BRL")

    assert op.fund_group_id == fund.id
    assert op.fund_group_out_id == brasil.id


def test_la_cotizacion_del_bot_nace_con_el_fondo_del_par(db, fund, pairs, client):
    from app.schemas.whatsapp import WhatsAppOperationCreate
    from app.services.whatsapp_quote_service import WhatsAppQuoteService

    pairs["ZELLE-VES"].default_fund_in_id = fund.id
    db.flush()

    op = WhatsAppQuoteService(db).create_quote(WhatsAppOperationCreate(
        client_phone=client.phone, client_display_name="Naldin",
        from_currency="ZELLE", to_currency="VES", amount=100, amount_side="SEND",
    ))
    db.refresh(op)

    assert op.fund_group_id == fund.id
    assert op.fund_group_out_id is None


def test_un_par_sin_fondo_da_una_op_sin_fondo_aunque_haya_fondo_de_esa_moneda(
    db, fund, pairs, client, operator
):
    """USD-VES es efectivo: el fondo en dólares existe, pero el par no lo pide."""
    op = _op_from_incoming(db, client, operator, "ZELLE", "VES")

    assert op.fund_group_id is None
    assert op.fund_group_out_id is None


def test_el_fondo_del_comprobante_le_gana_al_del_par(db, fund, pairs, client, operator):
    otro = FundGroup(name="Otro USD", currency="USD", is_active=True)
    db.add(otro)
    pairs["ZELLE-BRL"].default_fund_in_id = fund.id
    db.flush()

    op = _op_from_incoming(db, client, operator, "ZELLE", "BRL", fund_uuid=otro.uuid)

    assert op.fund_group_id == otro.id


def test_un_fondo_por_defecto_inactivo_no_se_asigna(db, fund, pairs, client, operator):
    fund.is_active = False
    pairs["ZELLE-BRL"].default_fund_in_id = fund.id
    db.flush()

    op = _op_from_incoming(db, client, operator, "ZELLE", "BRL")

    assert op.fund_group_id is None


# --------------------------------------------------------------- reparto de la ganancia

def test_el_7_3_del_par_se_aplica_tal_cual_aunque_se_cobre_8(db, fund, brasil, pairs, client):
    pair = _split_7_3(db, pairs, fund, brasil)
    op = _op(db, pair, client, fund, brasil, charged=8)
    svc = ProfitAllocationService(db)

    allocations = svc.ensure_defaults(op)

    assert {a.fund_group_id: a.percentage for a in allocations} == {fund.id: 7, brasil.id: 3}
    assert all(a.notes == PAIR_DEFAULT_NOTE for a in allocations)
    assert svc.unallocated_percentage(op) == -2


def test_el_porcentaje_va_al_fondo_que_la_pata_tiene_de_verdad(db, fund, brasil, pairs, client):
    """El comprobante cambió la entrada: el 7% va a ese fondo, no al del par."""
    pair = _split_7_3(db, pairs, fund, brasil)
    otro = FundGroup(name="Otro USD", currency="USD", is_active=True)
    db.add(otro)
    db.flush()
    op = _op(db, pair, client, otro, brasil, charged=10)

    allocations = ProfitAllocationService(db).ensure_defaults(op)

    assert {a.fund_group_id: a.percentage for a in allocations} == {otro.id: 7, brasil.id: 3}


def test_una_pata_sin_fondo_no_reparte_su_porcentaje(db, fund, brasil, pairs, client):
    pair = _split_7_3(db, pairs, fund, brasil)
    op = _op(db, pair, client, fund, None, charged=10)

    allocations = ProfitAllocationService(db).ensure_defaults(op)

    assert [(a.fund_group_id, a.percentage) for a in allocations] == [(fund.id, 7)]


def test_ajustar_a_lo_cobrado_reescala_y_firma(db, fund, brasil, pairs, client, operator):
    pair = _split_7_3(db, pairs, fund, brasil)
    op = _op(db, pair, client, fund, brasil, charged=8)
    svc = ProfitAllocationService(db)
    svc.ensure_defaults(op)

    allocations = svc.scale_to_charged(op, actor=operator)

    assert {a.fund_group_id: a.percentage for a in allocations} == {fund.id: 5.6, brasil.id: 2.4}
    assert svc.unallocated_percentage(op) == 0
    assert all(a.approved_by_user_id == operator.id for a in allocations)


def test_ajustar_sin_reparto_o_sin_cobro_se_rechaza(db, fund, brasil, pairs, client, operator):
    pair = _split_7_3(db, pairs, fund, brasil)
    svc = ProfitAllocationService(db)

    sin_reparto = _op(db, pair, client, None, None, charged=8)
    with pytest.raises(ValueError):
        svc.scale_to_charged(sin_reparto, actor=operator)

    sin_cobro = _op(db, pair, client, fund, brasil, charged=0)
    svc.ensure_defaults(sin_cobro)
    with pytest.raises(ValueError):
        svc.scale_to_charged(sin_cobro, actor=operator)


# --------------------------------------------------------------- configuración del par

def _update(db, pair, **fields):
    return asyncio.run(
        CurrencyPairRepository(db).update_currency_pair(pair.id, CurrencyPairUpdate(**fields))
    )


def test_un_porcentaje_sin_fondo_se_rechaza(db, pairs):
    with pytest.raises(ValueError):
        _update(db, pairs["ZELLE-BRL"], default_fund_in_profit_pct=7)


def test_configurar_el_par_y_editar_otra_cosa_no_la_borra(db, fund, brasil, pairs):
    pair = pairs["ZELLE-BRL"]
    _update(
        db, pair,
        default_fund_in_uuid=fund.uuid, default_fund_in_profit_pct=7,
        default_fund_out_uuid=brasil.uuid, default_fund_out_profit_pct=3,
    )

    updated = _update(db, pair, description="cambio de otra cosa")

    assert updated.default_fund_in_id == fund.id
    assert updated.default_fund_out_id == brasil.id
    assert updated.dict()["default_fund_out_name"] == "Cambios Brasil"
    assert updated.dict()["default_fund_in_profit_pct"] == 7


def test_mandar_el_fondo_en_null_lo_quita(db, fund, pairs):
    pair = pairs["ZELLE-BRL"]
    _update(db, pair, default_fund_in_uuid=fund.uuid)

    updated = _update(db, pair, default_fund_in_uuid=None)

    assert updated.default_fund_in_id is None


# --------------------------------------------------------------- migración

def _migration():
    path = next(Path(__file__).parents[1].glob("alembic/versions/f2a3b4c5d6e7_*.py"))
    spec = importlib.util.spec_from_file_location("default_funds_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_la_migracion_deja_a_cada_par_con_el_fondo_que_usa_hoy(db, fund, brasil, pairs):
    from tests.conftest import _pair

    colombia = FundGroup(name="Cambios Colombia", currency="COP", is_active=True)
    db.add(colombia)
    db.flush()
    usd_ves = _pair(db, "USD", "VES", 950)

    _migration().seed_default_funds(db.connection())
    db.expire_all()

    zelle_brl = pairs["ZELLE-BRL"]
    assert (zelle_brl.default_fund_in_id, zelle_brl.default_fund_out_id) == (fund.id, brasil.id)
    assert (zelle_brl.default_fund_in_profit_pct, zelle_brl.default_fund_out_profit_pct) == (7, 3)
    assert pairs["ZELLE-VES"].default_fund_in_id == fund.id
    assert pairs["ZELLE-VES"].default_fund_out_id is None
    assert pairs["COP-VES"].default_fund_in_id == colombia.id
    assert pairs["ZELLE-VES"].default_fund_in_profit_pct is None
    # Efectivo: sin fondo, aunque exista el fondo en dólares.
    assert usd_ves.default_fund_in_id is None
    # Excepciones donde la moneda no acierta: USDT no tiene fondo propio, pero los reales
    # salen del de Brasil; y ZELLE-COP paga con los pesos de Colombia.
    assert pairs["USDT-BRL"].default_fund_in_id is None
    assert pairs["USDT-BRL"].default_fund_out_id == brasil.id
    assert pairs["ZELLE-COP"].default_fund_in_id == fund.id
    assert pairs["ZELLE-COP"].default_fund_out_id == colombia.id
