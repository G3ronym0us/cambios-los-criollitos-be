"""
De qué mensaje de WhatsApp nació una operación. El mapa vivía en el SQLite del bot
(`quote_message_map`); de él dependen el aviso de revoke, la cancelación de la cotización
reemplazada por una corrección y el etiquetado VIA_PARTNER de la op de ESE mensaje.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError, WhatsAppQuoteService
from tests import factories as f


@pytest.fixture
def quotes(db):
    return WhatsAppQuoteService(db)


@pytest.fixture
def payments(db):
    return WhatsAppPaymentService(db)


def _op(db, payments, fund, operator, amount=100):
    inc = f.incoming(db, amount, "ZELLE")
    created = f.create_op_from_payment(
        payments, "incoming", inc, frm="ZELLE", to="BRL", from_amount=amount, to_amount=amount * 4.57,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(created["uuid"])).first()


def test_a_message_finds_the_operation_it_created(quotes, payments, db, fund, client, operator):
    op = _op(db, payments, fund, operator)
    quotes.record_source_message(op.uuid, "false_58412@c.us_3EB0A1", "584121112233")

    found = quotes.find_by_source_messages(["false_58412@c.us_3EB0A1"])
    assert str(found.operation.uuid) == str(op.uuid)
    assert found.client_phone == "584121112233"


def test_an_unknown_message_finds_nothing(quotes, db):
    assert quotes.find_by_source_messages(["nunca_visto"]) is None


def test_empty_candidates_find_nothing(quotes, db):
    """El bot manda los ids que alcanzó a capturar; a veces no capturó ninguno."""
    assert quotes.find_by_source_messages([]) is None
    assert quotes.find_by_source_messages(["", None]) is None


def test_the_candidate_order_decides(quotes, payments, db, fund, client, operator):
    """El revoke prueba varios ids del MISMO mensaje: gana el primero de la lista."""
    first = _op(db, payments, fund, operator, 100)
    second = _op(db, payments, fund, operator, 200)
    quotes.record_source_message(first.uuid, "id_A", "584121112233")
    quotes.record_source_message(second.uuid, "id_B", "584121112233")

    assert str(quotes.find_by_source_messages(["id_B", "id_A"]).operation.uuid) == str(second.uuid)
    assert str(quotes.find_by_source_messages(["id_A", "id_B"]).operation.uuid) == str(first.uuid)


def test_recording_the_same_message_again_repoints_it(quotes, payments, db, fund, client, operator):
    """Un mensaje origina UNA operación: si se reengancha, apunta a la nueva, no se duplica."""
    from app.models.whatsapp_operation_message import WhatsAppOperationMessage

    first = _op(db, payments, fund, operator, 100)
    second = _op(db, payments, fund, operator, 200)
    quotes.record_source_message(first.uuid, "id_A", "584121112233")
    quotes.record_source_message(second.uuid, "id_A", "584121112233")

    assert db.query(WhatsAppOperationMessage).filter(
        WhatsAppOperationMessage.wa_message_id == "id_A").count() == 1
    assert str(quotes.find_by_source_messages(["id_A"]).operation.uuid) == str(second.uuid)


def test_recording_against_a_missing_operation_is_a_404(quotes, db):
    from uuid import uuid4

    with pytest.raises(QuoteServiceError) as exc:
        quotes.record_source_message(uuid4(), "id_A", "584121112233")
    assert exc.value.http_status == 404


def test_deleting_the_operation_takes_its_messages(quotes, payments, db, fund, client, operator):
    """La fila no sobrevive a su operación: el FK va en CASCADE."""
    from app.models.whatsapp_operation_message import WhatsAppOperationMessage

    op = _op(db, payments, fund, operator)
    quotes.record_source_message(op.uuid, "id_A", "584121112233")
    db.delete(op)
    db.flush()

    assert db.query(WhatsAppOperationMessage).filter(
        WhatsAppOperationMessage.wa_message_id == "id_A").count() == 0
