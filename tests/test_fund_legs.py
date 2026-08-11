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


# --------------------------------------------------------------- _sync_fund_legs

def _op_completada(db, pairs, client, fund_in, fund_out, operator):
    from datetime import timedelta

    from app.models.transaction import Transaction, TransactionStatus
    from app.models.whatsapp_operation import (
        WhatsAppAmountSide, WhatsAppOperation, WhatsAppOperationStatus,
    )

    now = datetime.now(timezone.utc)
    # Toda operación COMPLETED en este sistema tiene su transacción ya creada (es lo que la
    # completa); `_sync_fund_legs` matchea los movimientos existentes por `transaction_id`,
    # así que sin uno cada corrida los trataría como nuevos y duplicaría.
    tx = Transaction(
        user_id=operator.id, from_amount=100, to_amount=465.75,
        exchange_rate=4.6575, status=TransactionStatus.COMPLETED,
    )
    db.add(tx)
    db.flush()

    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-BRL"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency="ZELLE",
        amount_usdt=100, usdt_rate=1,
        fund_group_id=fund_in.id if fund_in else None,
        fund_group_out_id=fund_out.id if fund_out else None,
        received_by_user_id=operator.id, transaction_id=tx.id,
        created_at=now, quoted_at=now, valuation_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    return op


def test_una_operacion_de_dos_fondos_deja_las_dos_patas(db, pairs, client, fund, operator):
    """El caso 3251: entran 100 USD al fondo Zelle, salen 465,75 BRL del de Brasil."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, brasil, operator)

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    movs = db.query(FundMovement).all()
    entra = [m for m in movs if m.movement_type == FundMovementType.EXCHANGE_IN]
    sale = [m for m in movs if m.movement_type == FundMovementType.EXCHANGE]

    assert len(entra) == 1 and entra[0].group_id == fund.id
    assert entra[0].amount == 100 and entra[0].currency == "USD"
    assert len(sale) == 1 and sale[0].group_id == brasil.id
    assert sale[0].amount == 465.75 and sale[0].currency == "BRL"


def test_la_pata_sin_fondo_no_deja_movimiento(db, pairs, client, fund, operator):
    """El caso normal: pagamos en bolívares, que no tienen fondo."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    movs = db.query(FundMovement).all()
    assert len(movs) == 1 and movs[0].movement_type == FundMovementType.EXCHANGE_IN


def test_correrlo_dos_veces_no_duplica(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppPaymentService(db)

    svc._sync_fund_legs(op, operator)
    svc._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 1


def test_quitarle_el_fondo_borra_su_pata(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppPaymentService(db)
    svc._sync_fund_legs(op, operator)

    op.fund_group_id = None
    db.flush()
    svc._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 0


def test_una_operacion_no_completada_no_mueve_el_fondo(db, pairs, client, fund, operator):
    """Una cotización no movió plata todavía."""
    from app.models.whatsapp_operation import WhatsAppOperationStatus
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    op = _op_completada(db, pairs, client, fund, None, operator)
    op.status = WhatsAppOperationStatus.QUOTED
    db.flush()

    WhatsAppPaymentService(db)._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 0


def test_cambiar_el_valor_reajusta_las_dos_patas(db, pairs, client, fund, operator):
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, brasil, operator)
    svc = WhatsAppPaymentService(db)
    svc._sync_fund_legs(op, operator)

    op.from_amount = 50
    op.to_amount = 232.88
    db.flush()
    svc._sync_fund_legs(op, operator)

    montos = {m.movement_type: m.amount for m in db.query(FundMovement).all()}
    assert montos[FundMovementType.EXCHANGE_IN] == 50
    assert montos[FundMovementType.EXCHANGE] == 232.88


def test_una_operacion_nueva_resuelve_sus_fondos_sola(db, fund, pairs, client, operator):
    """Nace con los fondos puestos según la moneda de cada pata."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()

    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=100, to_amount=465.75,
        user_uuid=operator.uuid, recorded_by=operator.id,
    )

    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()

    assert op.fund_group_id == fund.id
    assert op.fund_group_out_id == brasil.id


def test_el_fondo_elegido_a_mano_le_gana_a_la_resolucion(db, fund, pairs, client, operator):
    """Si el caller dice el fondo de entrada, no se pisa."""
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    otro = FundGroup(name="Otro", currency="USD", is_active=True)
    db.add(otro)
    db.flush()

    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "ZELLE", phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="ZELLE", to="BRL",
        from_amount=100, to_amount=465.75,
        fund_uuid=otro.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )

    from app.models.whatsapp_operation import WhatsAppOperation
    op = db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()

    assert op.fund_group_id == otro.id


# --------------------------------------------------------------- El hueco entre Task 4 y 5

def test_solo_la_pata_saliente_resuelve_fondo_igual_crea_transaccion_y_movimiento(
    db, pairs, client, operator
):
    """
    Entra USDT (ninguna moneda de la op tiene fondo USDT) y sale BRL (con fondo, `brasil`).
    Antes la transacción solo se creaba cuando el fondo ENTRANTE venía explícito (`if group
    is not None`, con `group` resuelto ANTES de crear la op); con la resolución automática de
    las dos patas eso dejaba a esta op sin transacción pese a tener fondo en la pata saliente.

    `table="incoming"` es a propósito: para "outgoing" con moneda distinta de USD la función
    crea una transacción por su cuenta (para completar la op de inmediato) sin pasar por el
    bloque que se está probando, y eso enmascararía el bug.
    """
    from datetime import timedelta

    from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
    from app.services.whatsapp_payment_service import WhatsAppPaymentService
    from tests import factories as f

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()

    svc = WhatsAppPaymentService(db)
    inc = f.incoming(db, 100, "USDT", phone=client.phone)
    created = f.create_op_from_payment(
        svc, "incoming", inc, frm="USDT", to="BRL",
        from_amount=100, to_amount=465.75,
        user_uuid=operator.uuid, recorded_by=operator.id,
    )

    op = db.query(WhatsAppOperation).filter(
        WhatsAppOperation.uuid == str(created["uuid"])
    ).first()

    assert op.fund_group_id is None
    assert op.fund_group_out_id == brasil.id
    assert op.transaction_id is not None

    # Completarla deja la pata saliente en el libro.
    op.status = WhatsAppOperationStatus.COMPLETED
    db.flush()
    svc._sync_fund_legs(op, operator)

    movs = db.query(FundMovement).filter(FundMovement.transaction_id == op.transaction_id).all()
    assert len(movs) == 1
    assert movs[0].movement_type == FundMovementType.EXCHANGE
    assert movs[0].group_id == brasil.id
    assert movs[0].amount == 465.75


def test_sin_transaction_id_no_registra_movimientos_ni_duplica(db, pairs, client, fund, operator):
    """
    Un movimiento siempre cuelga de una transacción — es lo que lo hace desaparecer por
    CASCADE si la op se borra. Con `transaction_id` en NULL, `_sync_fund_legs` no tiene con
    qué correlacionar lo existente; sin el guard crearía un movimiento suelto, y otro
    distinto en cada corrida porque nunca se encontraría a sí mismo la próxima vez.
    """
    from datetime import timedelta

    from app.models.whatsapp_operation import (
        WhatsAppAmountSide, WhatsAppOperation, WhatsAppOperationStatus,
    )
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    now = datetime.now(timezone.utc)
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs["ZELLE-BRL"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency="ZELLE",
        amount_usdt=100, usdt_rate=1,
        fund_group_id=fund.id, received_by_user_id=operator.id,
        created_at=now, quoted_at=now, valuation_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    assert op.transaction_id is None

    svc = WhatsAppPaymentService(db)
    svc._sync_fund_legs(op, operator)
    svc._sync_fund_legs(op, operator)

    assert db.query(FundMovement).count() == 0


def test_asignar_el_fondo_saliente_a_mano_crea_su_pata(db, fund, pairs, client, operator):
    from app.schemas.whatsapp import WhatsAppOperationScenarioUpdate
    from app.services.whatsapp_quote_service import WhatsAppQuoteService

    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op = _op_completada(db, pairs, client, fund, None, operator)

    WhatsAppQuoteService(db).set_scenario(
        op.uuid,
        WhatsAppOperationScenarioUpdate(fund_group_out_uuid=brasil.uuid),
        operator,
    )

    sale = db.query(FundMovement).filter(
        FundMovement.movement_type == FundMovementType.EXCHANGE
    ).all()
    assert len(sale) == 1 and sale[0].group_id == brasil.id


def test_quitar_el_fondo_a_mano_borra_su_pata(db, fund, pairs, client, operator):
    from app.schemas.whatsapp import WhatsAppOperationScenarioUpdate
    from app.services.whatsapp_quote_service import WhatsAppQuoteService

    op = _op_completada(db, pairs, client, fund, None, operator)
    svc = WhatsAppQuoteService(db)
    svc.set_scenario(op.uuid, WhatsAppOperationScenarioUpdate(), operator)

    svc.set_scenario(
        op.uuid, WhatsAppOperationScenarioUpdate(clear_fund_group=True), operator
    )

    assert db.query(FundMovement).count() == 0
