"""
Borrado de una operación sin comprobantes: se lleva su transacción y el movimiento del fondo,
pero se niega si aún tiene pagos o si movió el saldo del cliente. (La guardia al desvincular
el último pago vive en test_operation_value_model.)
"""

import pytest

from app.models.fund import FundMovement
from app.models.transaction import Transaction
from app.models.whatsapp_balance import WhatsAppBalanceEntry, WhatsAppBalanceEntryType
from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _orphan_op(service, db, fund, operator):
    """Una op ya sin comprobantes: se crea desde un saliente y luego se desvincula (KEEP)."""
    brl = f.outgoing(db, 914.04, "BRL")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", brl, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    tx_id = op.transaction_id
    service.set_operation("outgoing", brl.id, None, completing_user=operator, orphan_action="KEEP")
    db.refresh(op)
    return op, tx_id


def test_delete_operation_takes_transaction_and_fund_movement(service, db, fund, client, operator):
    op, tx_id = _orphan_op(service, db, fund, operator)
    assert db.query(FundMovement).filter(FundMovement.transaction_id == tx_id).count() == 1

    WhatsAppQuoteService(db).delete_operation(op)

    assert db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op.id).first() is None
    assert db.query(Transaction).filter(Transaction.id == tx_id).first() is None
    assert db.query(FundMovement).filter(FundMovement.transaction_id == tx_id).count() == 0


def test_delete_operation_refuses_while_it_has_payments(service, db, fund, client, operator):
    brl = f.outgoing(db, 914.04, "BRL")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", brl, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    with pytest.raises(QuoteServiceError) as exc:
        WhatsAppQuoteService(db).delete_operation(op)
    assert exc.value.code == "operation_has_payments"


def test_delete_operation_refuses_when_it_moved_client_balance(service, db, fund, client, operator):
    op, _ = _orphan_op(service, db, fund, operator)
    db.add(WhatsAppBalanceEntry(
        client_id=op.client_id, entry_type=WhatsAppBalanceEntryType.DEBIT,
        amount=20, currency="USD", whatsapp_operation_id=op.id,
    ))
    db.flush()
    with pytest.raises(QuoteServiceError) as exc:
        WhatsAppQuoteService(db).delete_operation(op)
    assert exc.value.code == "operation_has_balance_entries"
