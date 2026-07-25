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


# --------------------------------------------------------------------- reversa de movimientos

def _movement(db, group, user, kind, amount, when=None):
    from datetime import datetime, timezone
    from app.models.fund import FundMovement
    row = FundMovement(
        group_id=group.id, user_id=user.id, movement_type=kind,
        amount=amount, currency="USD", amount_usdt=amount,
        movement_date=when or datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    return row


def test_reversing_a_deposit_undoes_it_without_erasing_it(db, fund, operator):
    """El original se queda en el libro y el saldo vuelve a donde estaba."""
    from app.models.fund import FundMovementType
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    deposit = _movement(db, fund, operator, FundMovementType.DEPOSIT, 500)
    assert repo.get_group_balance(fund.id)["total_position_usdt"] == pytest.approx(500)

    reversal = repo.reverse_movement(deposit, reason="cargado dos veces", actor_id=operator.id)

    assert repo.get_group_balance(fund.id)["total_position_usdt"] == pytest.approx(0)
    db.refresh(deposit)
    assert deposit.reversed_by_movement_id == reversal.id  # sigue ahí, marcado
    assert deposit.reversed_at is not None
    assert reversal.movement_type == FundMovementType.DEPOSIT  # mismo tipo, signo opuesto
    assert reversal.notes == "cargado dos veces"
    assert reversal.recorded_by_user_id == operator.id


def test_reversing_an_outflow_gives_the_money_back(db, fund, operator):
    from app.models.fund import FundMovementType
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    _movement(db, fund, operator, FundMovementType.DEPOSIT, 500)
    personal = _movement(db, fund, operator, FundMovementType.PERSONAL, 200)
    assert repo.get_group_balance(fund.id)["total_position_usdt"] == pytest.approx(300)

    repo.reverse_movement(personal, reason="no era gasto del fondo", actor_id=operator.id)
    assert repo.get_group_balance(fund.id)["total_position_usdt"] == pytest.approx(500)


def test_the_statement_shows_the_balance_going_back(db, fund, operator):
    """En el extracto, la reversa aparece como una línea más que devuelve el saldo."""
    from datetime import datetime, timedelta, timezone
    from app.models.fund import FundMovementType
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    base = datetime.now(timezone.utc) - timedelta(days=2)
    deposit = _movement(db, fund, operator, FundMovementType.DEPOSIT, 500, when=base)
    reversal = repo.reverse_movement(deposit, reason="duplicado", actor_id=operator.id)

    movements, _ = repo.get_movements(group_id=fund.id)
    running = repo.get_running_totals(fund.id, [m.id for m in movements])

    assert running[deposit.id]["balance_usdt"] == pytest.approx(500)   # antes de anularlo
    assert running[reversal.id]["balance_usdt"] == pytest.approx(0)    # después
    assert len(movements) == 2  # las dos líneas quedan visibles


def test_you_can_jump_from_an_annulled_movement_to_its_correction(db, fund, operator):
    """
    La reversa se fecha el día de la corrección, así que el par casi nunca cae en la misma
    página. Cada lado tiene que saber en cuál está el otro.
    """
    from datetime import datetime, timedelta, timezone
    from app.models.fund import FundMovementType
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    viejo = _movement(db, fund, operator, FundMovementType.EXCHANGE, 320,
                      when=datetime.now(timezone.utc) - timedelta(days=30))
    # Movimientos más recientes que empujan al original fuera de la primera página.
    for i in range(3):
        _movement(db, fund, operator, FundMovementType.DEPOSIT, 10 + i,
                  when=datetime.now(timezone.utc) - timedelta(days=i))
    reversa = repo.reverse_movement(viejo, reason="nunca se ejecutó", actor_id=operator.id)

    # De ida y de vuelta, con páginas de 2 para que el par quede separado.
    assert repo.locate_movement(reversa, per_page=2) == 1
    assert repo.locate_movement(viejo, per_page=2) == 3
    db.refresh(viejo)
    assert viejo.reversed_by_movement_id == reversa.id
    assert reversa.reverses_movement_id == viejo.id


def test_a_movement_outside_the_filter_has_no_page(db, fund, operator):
    """Si el filtro deja fuera al otro lado del par, no hay página a la que saltar."""
    from datetime import datetime, timedelta, timezone
    from app.models.fund import FundMovementType
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    viejo = _movement(db, fund, operator, FundMovementType.PERSONAL, 40,
                      when=datetime.now(timezone.utc) - timedelta(days=10))
    reversa = repo.reverse_movement(viejo, reason="no era del fondo", actor_id=operator.id)

    corte = datetime.now(timezone.utc) - timedelta(days=5)
    assert repo.locate_movement(viejo, date_to=corte) == 1
    assert repo.locate_movement(reversa, date_to=corte) is None
