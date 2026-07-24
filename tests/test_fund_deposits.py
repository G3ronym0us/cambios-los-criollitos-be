"""
Depósitos al fondo: el ÚNICO camino a un FundMovement DEPOSIT es un pendiente confirmado
(detectado en el grupo o cargado a mano). Y un comprobante que ya está contabilizado como
pago entrante del cliente no debe contarse otra vez como depósito.
"""

import pytest

from app.models.fund import FundMovement, FundMovementType, FundPendingDepositStatus
from app.services.fund_pending_deposit_service import FundPendingDepositService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def deposits(db):
    return FundPendingDepositService(db)


def _confirmed_movements(db):
    return db.query(FundMovement).filter(FundMovement.movement_type == FundMovementType.DEPOSIT).all()


def test_group_deposit_confirmed_creates_a_fund_movement(deposits, db, fund, operator):
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    pending = deposits.create_pending(
        group_jid="120363@g.us", amount=1100, currency="ZELLE", provider="zelle",
        raw_text="Su pago fue enviado $1,100.00",
    )
    assert pending["origin"] == "GROUP" and pending["status"] == "PENDING"

    deposits.confirm(pending["uuid"], deposit_method="ZELLE", recorded_by_user_id=operator.id,
                     user_uuid=operator.uuid)
    movs = _confirmed_movements(db)
    assert len(movs) == 1 and movs[0].amount == 1100 and movs[0].group_id == fund.id


def test_manual_deposit_is_another_door_to_the_same_flow(deposits, db, fund, operator):
    pending = deposits.create_manual(
        group_uuid=fund.uuid, user_uuid=operator.uuid, amount=650, currency="USD",
        created_by_user_id=operator.id, notes="repuso sin postear",
    )
    assert pending["origin"] == "MANUAL"
    deposits.confirm(pending["uuid"], deposit_method="TRANSFER", recorded_by_user_id=operator.id)
    assert len(_confirmed_movements(db)) == 1


def test_deposit_duplicating_an_incoming_payment_is_blocked(deposits, db, fund, operator):
    """El gestor reenvía al grupo el Zelle de un cliente: ese dinero ya entró como pago."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    f.incoming(db, 325, "ZELLE", reference="ref-dup-1")
    pending = deposits.create_pending(
        group_jid="120363@g.us", amount=325, currency="ZELLE", reference="ref-dup-1",
    )
    assert pending["source_incoming_payment_id"] is not None

    with pytest.raises(QuoteServiceError) as exc:
        deposits.confirm(pending["uuid"], deposit_method="ZELLE", recorded_by_user_id=operator.id,
                         user_uuid=operator.uuid)
    assert exc.value.code == "duplicate_of_incoming"

    # Forzado (el operador lo asume) sí crea el movimiento.
    deposits.confirm(pending["uuid"], deposit_method="ZELLE", recorded_by_user_id=operator.id,
                     user_uuid=operator.uuid, override_duplicate=True)
    assert len(_confirmed_movements(db)) == 1


def test_rejecting_a_pending_deposit_creates_no_movement(deposits, db, fund, operator):
    pending = deposits.create_manual(
        group_uuid=fund.uuid, user_uuid=operator.uuid, amount=100, currency="USD",
        created_by_user_id=operator.id,
    )
    deposits.reject(pending["uuid"], resolved_by_user_id=operator.id)
    assert len(_confirmed_movements(db)) == 0
