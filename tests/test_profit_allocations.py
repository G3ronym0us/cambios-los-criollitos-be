"""
El reparto del margen de una operación: quién se queda con qué parte de lo que se le cobró
al cliente, y cómo eso se convierte en la ganancia de la transacción y de cada socio.

El caso corriente: se cobra 8%, el fondo se queda 7% y sus dos socios se lo parten 50/50.
Los otros son los que ya tiene el negocio: dos fondos sobre una misma operación, un pedazo
devuelto al cliente que hizo de intermediario, y repartir más de lo cobrado.
"""

import pytest

from app.models.profit_allocation import (
    OperationProfitAllocation,
    ProfitAllocationDestination,
)
from app.models.transaction import Transaction, TransactionProfitSplit
from app.models.whatsapp_operation import WhatsAppOperation
from app.repositories.fund_repository import FundRepository
from app.services.profit_allocation_service import ProfitAllocationService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _op_from_cop_payment(service, db, fund, operator, amount=100, to_amount=76360):
    """Una op ZELLE→COP: el par cobra 8% (tasa 763.6 sobre una base de 830)."""
    payout = f.outgoing(db, to_amount, "COP")
    return _op(db, f.create_op_from_payment(
        service, "outgoing", payout, frm="ZELLE", to="COP",
        from_amount=amount, to_amount=to_amount,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id,
    )["uuid"])


def _splits(db, op):
    return (
        db.query(TransactionProfitSplit)
        .filter(TransactionProfitSplit.transaction_id == op.transaction_id)
        .all()
    )


# --------------------------------------------------------------------- el caso corriente

def test_fund_keeps_its_percentage_and_partners_split_it(service, db, fund_with_shares, client, operator, partner):
    """Se cobra 8%, el fondo se queda 7% y jean/diohandres se lo parten 50/50."""
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)

    assert op.applied_percentage == 8.0  # al cliente se le cobró 8
    allocations = op.profit_allocations
    assert len(allocations) == 1
    assert allocations[0].destination_type == ProfitAllocationDestination.FUND
    assert allocations[0].percentage == 7.0
    assert allocations[0].amount_usdt == pytest.approx(7.0)  # 7% de 100 USDT

    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == 7.0
    assert tx.profit_amount == pytest.approx(7.0)

    splits = {s.user_id: s for s in _splits(db, op)}
    assert len(splits) == 2
    for user in (operator, partner):
        assert splits[user.id].profit_percentage == pytest.approx(3.5)
        assert splits[user.id].profit_amount == pytest.approx(3.5)
        assert splits[user.id].profit_amount_usdt == pytest.approx(3.5)


def test_the_unassigned_margin_is_visible(service, db, fund_with_shares, client, operator):
    """El 1% que no se lleva el fondo no desaparece: queda como margen sin asignar."""
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)
    assert ProfitAllocationService(db).unallocated_percentage(op) == pytest.approx(1.0)


def test_the_fund_never_takes_more_than_what_was_charged(service, db, fund_with_shares, client, operator):
    """El fondo pide 7% pero solo se cobró 5: se queda con 5, no inventa margen."""
    # 100 ZELLE a 788.5 COP = 5% sobre la base de 830.
    op = _op_from_cop_payment(service, db, fund_with_shares, operator, to_amount=78850)

    assert op.applied_percentage == 5.0
    assert op.profit_allocations[0].percentage == 5.0
    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == 5.0


def test_a_fund_without_a_default_keeps_everything_charged(service, db, fund, client, operator):
    """Un fondo sin porcentaje configurado se queda con todo lo cobrado (como antes)."""
    op = _op_from_cop_payment(service, db, fund, operator)
    assert op.profit_allocations[0].percentage == 8.0
    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == 8.0


def test_an_operation_without_a_fund_does_not_allocate(service, db, client, operator):
    """Sin fondo no hay a quién repartir: la ganancia sigue siendo el margen cobrado."""
    payout = f.outgoing(db, 76360, "COP")
    op = _op(db, f.create_op_from_payment(
        service, "outgoing", payout, frm="ZELLE", to="COP",
        from_amount=100, to_amount=76360, recorded_by=operator.id,
    )["uuid"])

    assert op.profit_allocations == []
    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == 8.0
    assert _splits(db, op) == []


# --------------------------------------------------------------------- los casos del negocio

def test_two_funds_share_one_operation(service, db, fund_with_shares, client, operator, partner):
    """ZELLE→BRL al 10%: 8% se queda en el fondo Zelle y 2% en el de Brasil."""
    from app.models.fund import FundGroup, FundGroupMember
    brasil = FundGroup(name="Cambios Brasil", currency="USD", is_active=True)
    db.add(brasil)
    db.flush()
    db.add(FundGroupMember(group_id=brasil.id, user_id=partner.id, profit_share_percentage=100.0))
    db.flush()

    op = _op_from_cop_payment(service, db, fund_with_shares, operator)
    ProfitAllocationService(db).set_allocations(op, [
        {"destination_type": "FUND", "fund_group_uuid": fund_with_shares.uuid, "percentage": 8.0},
        {"destination_type": "FUND", "fund_group_uuid": brasil.uuid, "percentage": 2.0},
    ], actor=operator)
    from app.services.whatsapp_quote_service import WhatsAppQuoteService
    WhatsAppQuoteService(db).resync_transaction(op)

    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == pytest.approx(10.0)
    assert tx.profit_amount == pytest.approx(10.0)  # 10% de 100 USDT

    splits = {s.user_id: s.profit_percentage for s in _splits(db, op)}
    assert splits[operator.id] == pytest.approx(4.0)   # 50% del 8 de Zelle
    assert splits[partner.id] == pytest.approx(6.0)    # 4 de Zelle + los 2 de Brasil


def test_a_slice_can_go_back_to_the_client(service, db, fund_with_shares, client, operator):
    """
    Se cobra 8%, el fondo se queda 7% y 1% se le devuelve al cliente que hizo de
    intermediario: eso NO es ganancia del negocio, así que no entra en la transacción.
    """
    from app.services.whatsapp_quote_service import WhatsAppQuoteService
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)

    ProfitAllocationService(db).set_allocations(op, [
        {"destination_type": "FUND", "fund_group_uuid": fund_with_shares.uuid, "percentage": 7.0},
        {"destination_type": "CLIENT", "client_uuid": op.client.uuid, "percentage": 1.0,
         "notes": "intermediario de un tercero"},
    ], actor=operator)
    WhatsAppQuoteService(db).resync_transaction(op)

    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.total_profit_percentage == 7.0  # solo lo del fondo
    assert ProfitAllocationService(db).unallocated_percentage(op) == pytest.approx(0.0)

    client_row = [
        a for a in op.profit_allocations
        if a.destination_type == ProfitAllocationDestination.CLIENT
    ][0]
    assert client_row.amount_usdt == pytest.approx(1.0)


def test_allocating_more_than_charged_is_signed_by_the_operator(service, db, fund_with_shares, client, operator):
    """Se cobró 8 pero se reparten 9: se permite y queda firmado quién lo aprobó."""
    from app.services.whatsapp_quote_service import WhatsAppQuoteService
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)

    ProfitAllocationService(db).set_allocations(op, [
        {"destination_type": "FUND", "fund_group_uuid": fund_with_shares.uuid, "percentage": 7.0},
        {"destination_type": "CLIENT", "client_uuid": op.client.uuid, "percentage": 2.0},
    ], actor=operator)
    WhatsAppQuoteService(db).resync_transaction(op)

    assert ProfitAllocationService(db).unallocated_percentage(op) == pytest.approx(-1.0)
    assert all(a.approved_by_user_id == operator.id for a in op.profit_allocations)
    assert all(a.approved_at is not None for a in op.profit_allocations)


# --------------------------------------------------------------------- consecuencias

def test_editing_the_value_moves_the_whole_chain(service, db, fund_with_shares, client, operator, partner):
    """Bajar el valor de la op recalcula el reparto, la transacción y lo de cada socio."""
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)
    service.set_operation_value(op.uuid, 50, actor=operator)
    db.refresh(op)

    assert op.profit_allocations[0].amount_usdt == pytest.approx(3.5)  # 7% de 50
    tx = db.query(Transaction).filter(Transaction.id == op.transaction_id).first()
    assert tx.profit_amount == pytest.approx(3.5)
    splits = {s.user_id: s.profit_amount for s in _splits(db, op)}
    assert splits[operator.id] == pytest.approx(1.75)
    assert splits[partner.id] == pytest.approx(1.75)


def test_the_fund_balance_counts_what_was_allocated_to_it(service, db, fund_with_shares, client, operator):
    """La «Acumulada» del fondo se mueve con lo que las operaciones le asignaron."""
    _op_from_cop_payment(service, db, fund_with_shares, operator)
    db.flush()

    balance = FundRepository(db).get_group_balance(fund_with_shares.id)
    assert balance["total_profit_usdt"] == pytest.approx(7.0)


def test_the_balance_does_not_count_the_same_profit_twice(service, db, fund_with_shares, client, operator):
    """Los splits de una op con reparto no se suman aparte: saldrían dos veces."""
    op = _op_from_cop_payment(service, db, fund_with_shares, operator)
    assert len(_splits(db, op)) == 2  # tiene splits, y aun así...

    balance = FundRepository(db).get_group_balance(fund_with_shares.id)
    assert balance["total_profit_usdt"] == pytest.approx(7.0)  # ...no 14


def test_history_totals_add_up_over_the_filter(service, db, fund_with_shares, client, operator):
    """
    Los acumulados del historial cubren todo lo filtrado: la ganancia por un lado y el
    capital (entradas contra salidas) por otro, que son cosas distintas.
    """
    from app.models.fund import FundMovement, FundMovementType
    from datetime import datetime, timezone

    _op_from_cop_payment(service, db, fund_with_shares, operator)  # 100 USDT, 7% al fondo
    db.add(FundMovement(
        group_id=fund_with_shares.id, user_id=operator.id,
        movement_type=FundMovementType.DEPOSIT, amount=500, currency="USD",
        amount_usdt=500, movement_date=datetime.now(timezone.utc),
    ))
    db.flush()

    totals = FundRepository(db).get_movements_totals(group_id=fund_with_shares.id)

    assert totals["deposits_usdt"] == pytest.approx(500)
    assert totals["exchanges_usdt"] == pytest.approx(100)   # el EXCHANGE de la operación
    assert totals["net_usdt"] == pytest.approx(400)         # entró 500, salieron 100
    assert totals["profit_usdt"] == pytest.approx(7.0)      # y dejó 7 de ganancia
    assert totals["profit_count"] == 1


def test_history_totals_follow_the_type_filter(service, db, fund_with_shares, client, operator):
    """Filtrando por un tipo, los acumulados hablan solo de ese tipo."""
    from app.models.fund import FundMovement, FundMovementType
    from datetime import datetime, timezone

    _op_from_cop_payment(service, db, fund_with_shares, operator)
    db.add(FundMovement(
        group_id=fund_with_shares.id, user_id=operator.id,
        movement_type=FundMovementType.DEPOSIT, amount=500, currency="USD",
        amount_usdt=500, movement_date=datetime.now(timezone.utc),
    ))
    db.flush()

    totals = FundRepository(db).get_movements_totals(
        group_id=fund_with_shares.id, movement_type=FundMovementType.DEPOSIT
    )
    assert totals["deposits_usdt"] == pytest.approx(500)
    assert totals["exchanges_usdt"] == 0
    assert totals["profit_usdt"] == 0  # un depósito no deja ganancia


def test_each_movement_carries_the_running_balance_and_profit(service, db, fund_with_shares, client, operator):
    """
    El historial es un extracto: cada movimiento dice cómo quedaba el fondo justo después.
    El acumulado del más reciente tiene que coincidir con la posición del grupo.
    """
    from app.models.fund import FundMovement, FundMovementType
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc) - timedelta(days=3)
    db.add(FundMovement(
        group_id=fund_with_shares.id, user_id=operator.id,
        movement_type=FundMovementType.DEPOSIT, amount=500, currency="USD",
        amount_usdt=500, movement_date=base,
    ))
    db.flush()
    _op_from_cop_payment(service, db, fund_with_shares, operator)  # EXCHANGE de 100, gana 7

    repo = FundRepository(db)
    movements, _ = repo.get_movements(group_id=fund_with_shares.id)
    running = repo.get_running_totals(fund_with_shares.id, [m.id for m in movements])

    # get_movements devuelve del más reciente al más viejo.
    newest, oldest = movements[0], movements[-1]
    assert running[oldest.id]["balance_usdt"] == pytest.approx(500)   # solo el depósito
    assert running[oldest.id]["profit_usdt"] == pytest.approx(0)
    assert running[newest.id]["balance_usdt"] == pytest.approx(400)   # 500 - 100
    assert running[newest.id]["profit_usdt"] == pytest.approx(7.0)

    balance = repo.get_group_balance(fund_with_shares.id)
    assert running[newest.id]["balance_usdt"] == pytest.approx(balance["total_position_usdt"])


def test_a_reversed_movement_stops_counting_its_profit(service, db, fund_with_shares, client, operator):
    """Anular un movimiento con ganancia también la saca de los acumulados."""
    from app.repositories.fund_repository import FundRepository

    repo = FundRepository(db)
    _op_from_cop_payment(service, db, fund_with_shares, operator)
    movement = repo.get_movements(group_id=fund_with_shares.id)[0][0]
    assert repo.get_movements_totals(group_id=fund_with_shares.id)["profit_usdt"] == pytest.approx(7)

    # (el guard del endpoint impide reversar movimientos de operación; aquí se prueba la suma)
    repo.reverse_movement(movement, reason="prueba", actor_id=operator.id)
    assert repo.get_movements_totals(group_id=fund_with_shares.id)["profit_usdt"] == pytest.approx(0)
