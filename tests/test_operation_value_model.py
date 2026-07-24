"""
El corazón del rediseño «operación por valor», probado de extremo a extremo contra Postgres:
el caso real Naldin (un Zelle de 220 pagado en BRL + VES) y sus variantes.

Cada test parte de comprobantes sueltos y arma la operación con los mismos servicios que usa
el panel, comprobando lo que el operador ve: valor, entregado/pendiente, estado, la
transacción y el movimiento del fondo.
"""

import pytest

from app.models.fund import FundMovement, FundMovementType
from app.models.transaction import Transaction
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_payment import WhatsAppPaymentAllocation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


# --------------------------------------------------------------------- valor de la operación

def test_op_created_from_incoming_stores_value_and_usdt(service, db, fund, client, operator):
    pay = f.incoming(db, 220, "ZELLE")
    res = f.create_op_from_payment(
        service, "incoming", pay, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    op = _op(db, res["uuid"])
    assert op.amount == 220 and op.currency == "ZELLE"
    assert op.amount_usdt == 220  # ZELLE liquida como USD → 1:1
    # La cotización queda como referencia, no como el valor.
    assert op.from_amount == 220 and op.to_amount == 1005.44


def test_op_created_from_outgoing_is_backed_by_that_payment(service, db, fund, client, operator):
    """Crear la op DESDE el Pix afirma que ese pago la cubre por su valor."""
    pix = f.outgoing(db, 914.04, "BRL")
    res = f.create_op_from_payment(
        service, "outgoing", pix, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )
    op = _op(db, res["uuid"])
    assert op.delivered_amount == 220 and op.dict()["pending_amount"] == 0
    db.refresh(pix)
    assert pix.settled_amount == 220


# --------------------------------------------------------------------- cobertura de salientes

def test_coverage_suggests_amount_from_rate(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    pix = f.outgoing(db, 914.04, "BRL")

    cov = service.coverage_preview(pix.id, op.uuid)
    assert cov["suggested_settled_amount"] == 200  # 914.04 / 4.5702
    assert cov["pending"] == 220
    # Si cubriera todo el pendiente: la tasa efectiva y la diferencia contra la de referencia.
    assert cov["full_effective_rate"] == pytest.approx(4.1547, abs=1e-3)
    assert cov["full_amount_difference"] == pytest.approx(-91.4, abs=0.1)


def test_two_payouts_cover_the_value_and_complete(service, db, fund, client, operator):
    """El caso Naldin: 914,04 BRL cubre 200, 15.658,4 VES cubre 20 → completa sola."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    brl = f.outgoing(db, 914.04, "BRL")
    ves = f.outgoing(db, 15658.4, "VES")

    service.set_operation("outgoing", brl.id, op.uuid, completing_user=operator, complete_outgoing=True)
    db.refresh(op)
    assert op.delivered_amount == 200 and op.status == WhatsAppOperationStatus.PENDING

    service.set_operation("outgoing", ves.id, op.uuid, completing_user=operator, complete_outgoing=True)
    db.refresh(op)
    assert op.delivered_amount == 220 and op.status == WhatsAppOperationStatus.COMPLETED


def test_payout_can_cover_full_value_with_explicit_amount(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    brl = f.outgoing(db, 914.04, "BRL")

    service.set_operation("outgoing", brl.id, op.uuid, completing_user=operator,
                          complete_outgoing=True, settled_amount=220)
    db.refresh(op); db.refresh(brl)
    assert op.status == WhatsAppOperationStatus.COMPLETED
    assert brl.settled_amount == 220
    assert brl.dict()["settled_rate"] == pytest.approx(4.1547, abs=1e-3)


# --------------------------------------------------------------------- editar el valor

def test_edit_value_down_resyncs_everything(service, db, fund, client, operator):
    """Bajar el valor recorta cobertura, transacción y movimiento del fondo (el bug reportado)."""
    brl = f.outgoing(db, 914.04, "BRL")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", brl, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    service.set_operation_value(op.uuid, 200, actor=operator)
    db.refresh(op); db.refresh(brl)

    assert op.amount == 200
    assert brl.settled_amount == 200                       # cobertura recortada
    assert brl.dict()["settled_rate"] == pytest.approx(4.5702, abs=1e-3)  # vuelve a la de referencia
    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.from_amount == 200 and tx.to_amount == 200 and tx.from_currency == "ZELLE"
    mv = db.query(FundMovement).filter(FundMovement.transaction_id == op.transaction_id).first()
    assert mv.amount == 200 and mv.amount_usdt == 200      # movimiento re-sincronizado


def test_edit_value_up_reopens_pending(service, db, fund, client, operator):
    brl = f.outgoing(db, 914.04, "BRL")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", brl, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.status == WhatsAppOperationStatus.COMPLETED

    service.set_operation_value(op.uuid, 220, actor=operator)
    db.refresh(op)
    assert op.amount == 220 and op.dict()["pending_amount"] == 20


# --------------------------------------------------------------------- contabilidad

def test_single_transaction_and_movement_per_operation(service, db, fund, client, operator):
    """Una op pagada en dos monedas = UNA transacción y UN movimiento, en la moneda del fondo."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    service.set_operation("outgoing", f.outgoing(db, 914.04, "BRL").id, op.uuid,
                          completing_user=operator, complete_outgoing=True)
    service.set_operation("outgoing", f.outgoing(db, 15658.4, "VES").id, op.uuid,
                          completing_user=operator, complete_outgoing=True)
    db.refresh(op)

    txs = db.query(Transaction).filter(Transaction.id == op.transaction_id).all()
    assert len(txs) == 1 and txs[0].from_amount == 220 and txs[0].to_currency == "USDT"
    movs = db.query(FundMovement).filter(
        FundMovement.transaction_id == op.transaction_id,
        FundMovement.movement_type == FundMovementType.EXCHANGE,
    ).all()
    assert len(movs) == 1 and movs[0].amount == 220 and movs[0].currency == "USD"


# --------------------------------------------------------------------- reparto de un entrante

def test_split_incoming_across_two_operations(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op_a = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    # Al crear la op de 200 desde el entrante de 220, quedan 20 sin asignar.
    summary = service.allocation_summary(inc.id)
    assert summary["assigned"] == 200 and summary["unassigned"] == 20

    ves = f.outgoing(db, 15658.4, "VES")
    op_b = _op(db, f.create_op_from_payment(
        service, "outgoing", ves, frm="ZELLE", to="VES", from_amount=20, to_amount=15658.4,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    summary = service.set_allocations(inc.id, [
        _item(op_a.uuid, 200), _item(op_b.uuid, 20),
    ], actor=operator)
    assert summary["unassigned"] == 0 and len(summary["allocations"]) == 2
    assert db.query(WhatsAppPaymentAllocation).filter(
        WhatsAppPaymentAllocation.incoming_payment_id == inc.id).count() == 2


def test_allocation_cannot_exceed_payment(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    with pytest.raises(QuoteServiceError) as exc:
        service.set_allocations(inc.id, [_item(op.uuid, 250)], actor=operator)
    assert exc.value.code == "allocation_exceeds_payment"


# --------------------------------------------------------------------- desvincular sin respaldo

def test_unlinking_only_payment_requires_a_decision(service, db, fund, client, operator):
    brl = f.outgoing(db, 914.04, "BRL")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", brl, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    with pytest.raises(QuoteServiceError) as exc:
        service.set_operation("outgoing", brl.id, None, completing_user=operator)
    assert exc.value.code == "operation_would_be_orphan"

    # KEEP: la op se conserva firmada; DELETE_OPERATION la borra con su transacción.
    service.set_operation("outgoing", brl.id, None, completing_user=operator, orphan_action="KEEP")
    db.refresh(op)
    assert op.no_payments_ack_by_user_id == operator.id


def _item(op_uuid, amount):
    from types import SimpleNamespace
    return SimpleNamespace(operation_uuid=op_uuid, amount=amount)
