"""
Vincular el último saliente NO completa una operación de un par de efectivo, contra Postgres
real.

El caso (Gabriela Tejera, op 5239, 2026-10-08): 350 USD → 325.500 Bs en USD-VES, pagados en
dos salientes. Al vincular el segundo la operación pasó a COMPLETED y desapareció de «por
entregar», que en un par de efectivo sólo lista PENDING. Los bolívares son NUESTRA pata; lo
que cierra el trato es que el cliente traiga sus billetes (`collected_amount`).
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f
from tests.conftest import _pair


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


@pytest.fixture
def usd_ves(db):
    pair = _pair(db, "USD", "VES", 930.0)
    pair.settles_in_cash = True
    db.flush()
    return pair


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _two_payout_op(service, db, operator):
    first = f.outgoing(db, 190000, "VES")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", first, frm="USD", to="VES", from_amount=350, to_amount=325500,
        recorded_by=operator.id)["uuid"])
    second = f.outgoing(db, 135500, "VES")
    return op, second


def test_linking_the_last_payout_of_a_cash_pair_keeps_it_pending(
    service, db, usd_ves, operator
):
    op, second = _two_payout_op(service, db, operator)
    assert op.status == WhatsAppOperationStatus.PENDING

    service.set_operation("outgoing", second.id, op.uuid, completing_user=operator,
                          complete_outgoing=True)

    db.refresh(op)
    assert service.delivered_amount(op) == pytest.approx(350, abs=0.01)
    assert op.status == WhatsAppOperationStatus.PENDING
    assert op.to_collect == 350


def test_once_the_cash_is_collected_the_last_payout_does_complete_it(
    service, db, usd_ves, operator
):
    """Si el cliente ya pagó sus billetes, el último saliente sí cierra el trato."""
    op, second = _two_payout_op(service, db, operator)
    op.collected_amount = 350
    db.flush()

    service.set_operation("outgoing", second.id, op.uuid, completing_user=operator,
                          complete_outgoing=True)

    db.refresh(op)
    assert op.status == WhatsAppOperationStatus.COMPLETED
