"""El arrastre de los movimientos viejos a las dos patas."""

from datetime import datetime, timedelta, timezone

from app.cli.backfill_fund_legs import plan_backfill
from app.models.fund import FundGroup, FundMovement, FundMovementType
from app.models.transaction import Transaction
from app.models.whatsapp_operation import (
    WhatsAppAmountSide, WhatsAppOperation, WhatsAppOperationStatus,
)


def _op_con_movimiento(db, pairs, client, group, amount, currency, frm, to, usdt, user_id):
    """`plan_backfill` sólo mira movimientos EXCHANGE con `transaction_id`, y los liga a su
    operación por ese mismo id: hace falta una `Transaction` real de fondo para ambos."""
    now = datetime.now(timezone.utc)
    tx = Transaction(from_amount=100, to_amount=465.75)
    db.add(tx)
    db.flush()
    op = WhatsAppOperation(
        client_id=client.id, currency_pair_id=pairs[f"{frm}-{to}"].id,
        from_amount=100, to_amount=465.75, rate_used=4.6575, amount_side=WhatsAppAmountSide.SEND,
        status=WhatsAppOperationStatus.COMPLETED, amount=100, currency=frm,
        fund_group_id=group.id, transaction_id=tx.id, created_at=now, quoted_at=now,
        valuation_at=now, expires_at=now + timedelta(minutes=30),
    )
    db.add(op)
    db.flush()
    mov = FundMovement(
        group_id=group.id, user_id=user_id, movement_type=FundMovementType.EXCHANGE,
        amount=amount, currency=currency, amount_usdt=usdt, movement_date=now,
        transaction_id=tx.id,
    )
    db.add(mov)
    db.flush()
    return op, mov


def test_el_movimiento_de_la_pata_entrante_solo_cambia_de_tipo(db, fund, pairs, client, operator):
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "BRL", 100, operator.id)

    plan = plan_backfill(db)

    assert plan["retipar"] == [(mov.id, FundMovementType.EXCHANGE_IN)]
    assert plan["rehacer"] == []


def test_el_movimiento_en_la_moneda_del_otro_fondo_se_rehace(db, fund, pairs, client, operator):
    """El caso 6172: 106,85 BRL que en realidad son los 21 USD que entregó el cliente."""
    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op, mov = _op_con_movimiento(db, pairs, client, brasil, 106.85, "BRL", "ZELLE", "BRL", 21, operator.id)

    plan = plan_backfill(db)

    assert plan["retipar"] == []
    rehacer = plan["rehacer"][0]
    assert rehacer["movement_id"] == mov.id
    assert rehacer["group_id"] == fund.id      # pasa al fondo USD
    assert rehacer["currency"] == "USD"


def test_se_planifica_la_pata_saliente_que_falta(db, fund, pairs, client, operator):
    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "BRL", 100, operator.id)

    plan = plan_backfill(db)

    nueva = plan["crear_saliente"][0]
    assert nueva["operation_id"] == op.id
    assert nueva["group_id"] == brasil.id
    assert nueva["amount"] == 465.75


def test_una_op_que_paga_en_bolivares_no_genera_pata_saliente(db, fund, pairs, client, operator):
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "VES", 100, operator.id)

    plan = plan_backfill(db)

    assert plan["crear_saliente"] == []


def test_una_op_sin_completar_no_genera_pata_saliente(db, fund, pairs, client, operator):
    """
    Solo una op COMPLETED entregó la plata — la misma regla que `_sync_fund_legs` aplica en
    vivo. En producción 3 de las 4 candidatas estaban PENDING: sin este filtro el arrastre
    inventaba salidas por tratos que todavía no se pagaron.
    """
    brasil = FundGroup(name="Brasil", currency="BRL", is_active=True)
    db.add(brasil)
    db.flush()
    op, mov = _op_con_movimiento(db, pairs, client, fund, 100, "USD", "ZELLE", "BRL", 100, operator.id)
    op.status = WhatsAppOperationStatus.PENDING
    db.flush()

    plan = plan_backfill(db)

    assert plan["crear_saliente"] == []
    # La pata que YA existe se retipa igual: cambiarle el signo a lo que está mal registrado
    # es independiente de si el trato se terminó.
    assert plan["retipar"] == [(mov.id, FundMovementType.EXCHANGE_IN)]
