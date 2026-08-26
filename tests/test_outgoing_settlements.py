"""
Un comprobante de salida repartido entre varias operaciones, contra Postgres real.

El caso que lo pide (Nelson, 2026-08-25): el cliente manda dos Zelle —80 y 35— y se le paga
TODO en un solo envío de bolívares. Hasta ahora el saliente tenía un FK único: había que
elegir a cuál de los dos tratos vincularlo y dejar al otro sin comprobante.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.models.whatsapp_payment import WhatsAppOutgoingSettlement
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


class _Item:
    """Lo que el router entrega ya validado por pydantic."""

    def __init__(self, operation_uuid, settled_amount):
        self.operation_uuid = operation_uuid
        self.settled_amount = settled_amount


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _two_quotes(service, db, fund, operator):
    """Dos tratos del mismo cliente, 80 y 35 ZELLE, cada uno con su entrante."""
    inc80 = f.incoming(db, 80, "ZELLE")
    op80 = _op(db, f.create_op_from_payment(
        service, "incoming", inc80, frm="ZELLE", to="VES", from_amount=80, to_amount=62633.6,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    inc35 = f.incoming(db, 35, "ZELLE")
    op35 = _op(db, f.create_op_from_payment(
        service, "incoming", inc35, frm="ZELLE", to="VES", from_amount=35, to_amount=27402.2,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    return op80, op35


def test_one_payout_covers_two_operations(service, db, fund, client, operator):
    """Un solo saliente cubre los dos tratos y los completa a la vez."""
    op80, op35 = _two_quotes(service, db, fund, operator)
    payout = f.outgoing(db, 62633.6 + 27402.2, "VES")

    result = service.set_settlements(
        payout.id,
        [_Item(op80.uuid, 80), _Item(op35.uuid, 35)],
        actor=operator,
    )

    assert result["settled_total"] == 115
    assert len(result["settlements"]) == 2
    db.refresh(op80)
    db.refresh(op35)
    assert op80.status == WhatsAppOperationStatus.COMPLETED
    assert op35.status == WhatsAppOperationStatus.COMPLETED
    assert service.delivered_amount(op80) == 80
    assert service.delivered_amount(op35) == 35


def test_the_fk_points_at_the_biggest_share(service, db, fund, client, operator):
    """
    El FK del comprobante sigue existiendo y apunta a la parte mayor, como en los entrantes:
    el bot, el matcher y las listas lo leen y no tienen por qué enterarse del reparto.
    """
    op80, op35 = _two_quotes(service, db, fund, operator)
    payout = f.outgoing(db, 90035.8, "VES")

    service.set_settlements(
        payout.id, [_Item(op35.uuid, 35), _Item(op80.uuid, 80)], actor=operator
    )
    db.refresh(payout)
    assert payout.whatsapp_operation_id == op80.id
    # Y el total cubierto queda derivado del reparto, no escrito a mano.
    assert payout.settled_amount == 115


def test_a_single_destination_behaves_exactly_as_before(service, db, fund, client, operator):
    """El caso de siempre —un saliente, una operación— es este mismo con una sola fila."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    payout = f.outgoing(db, 1005.44, "BRL")
    service.set_operation("outgoing", payout.id, op.uuid, completing_user=operator,
                          complete_outgoing=True)

    db.refresh(payout)
    rows = db.query(WhatsAppOutgoingSettlement).filter(
        WhatsAppOutgoingSettlement.outgoing_payment_id == payout.id
    ).all()
    assert len(rows) == 1
    assert rows[0].whatsapp_operation_id == op.id
    assert payout.settled_amount == rows[0].settled_amount
    assert service.delivered_amount(op) == rows[0].settled_amount


def test_an_operation_cannot_be_covered_above_its_value(service, db, fund, client, operator):
    """Repartir de más sobre un trato es el error que sí importa atajar."""
    op80, op35 = _two_quotes(service, db, fund, operator)
    payout = f.outgoing(db, 200000, "VES")

    with pytest.raises(QuoteServiceError) as exc:
        service.set_settlements(
            payout.id, [_Item(op80.uuid, 200), _Item(op35.uuid, 35)], actor=operator
        )
    assert exc.value.code == "settlement_exceeds_operation"


def test_the_split_counts_what_other_payouts_already_cover(service, db, fund, client, operator):
    """Lo ya cubierto por OTRO comprobante de la misma op cuenta contra el tope."""
    op80, _ = _two_quotes(service, db, fund, operator)
    first = f.outgoing(db, 39146.0, "VES")
    service.set_settlements(first.id, [_Item(op80.uuid, 50)], actor=operator)

    second = f.outgoing(db, 39146.0, "VES")
    with pytest.raises(QuoteServiceError) as exc:
        service.set_settlements(second.id, [_Item(op80.uuid, 50)], actor=operator)
    assert exc.value.code == "settlement_exceeds_operation"

    # 30 sí caben: 50 + 30 = 80, el valor exacto del trato.
    service.set_settlements(second.id, [_Item(op80.uuid, 30)], actor=operator)
    assert service.delivered_amount(op80) == 80


def test_an_empty_split_is_refused(service, db, fund, client, operator):
    """Vaciar el reparto es desvincular, y eso tiene su propia confirmación."""
    op80, _ = _two_quotes(service, db, fund, operator)
    payout = f.outgoing(db, 62633.6, "VES")
    service.set_settlements(payout.id, [_Item(op80.uuid, 80)], actor=operator)

    with pytest.raises(QuoteServiceError) as exc:
        service.set_settlements(payout.id, [], actor=operator)
    assert exc.value.code == "settlements_empty"


def test_unlinking_releases_only_its_own_share(service, db, fund, client, operator):
    """Desvincular suelta la parte de la op principal; lo que cubría a la otra sigue en pie."""
    op80, op35 = _two_quotes(service, db, fund, operator)
    payout = f.outgoing(db, 90035.8, "VES")
    service.set_settlements(
        payout.id, [_Item(op80.uuid, 80), _Item(op35.uuid, 35)], actor=operator
    )

    service.set_operation("outgoing", payout.id, None, completing_user=operator,
                          orphan_action="KEEP", orphan_note="prueba")

    db.refresh(payout)
    assert service.delivered_amount(op80) == 0
    assert service.delivered_amount(op35) == 35
    # El FK pasa a la operación que el comprobante todavía cubre.
    assert payout.whatsapp_operation_id == op35.id
    assert payout.settled_amount == 35
