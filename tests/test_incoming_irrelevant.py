"""
`is_irrelevant` en pagos ENTRANTES — lo mismo que ya existía para salientes, llevado al otro
lado (el operador no tenía dónde clasificar «esto llegó al chat y no es un pago al negocio»).

El significado NO es el mismo que en salientes, y estos tests lo fijan:

- Un SALIENTE irrelevante es dinero NUESTRO que salió por algo que no es un cambio.
- Un ENTRANTE irrelevante nunca fue nuestro: una captura reenviada por error, el duplicado
  del mismo Zelle, plata que el cliente mandó por otra cosa. Por eso, a diferencia del
  saliente, un entrante marcado irrelevante NO deja necesariamente una operación huérfana:
  las operaciones de socio (`VIA_PARTNER`) y los pares que se cambian en efectivo nunca
  tienen entrante (`backend/CLAUDE.md`, sección «El negocio»), así que perder ESTE entrante
  y seguir teniendo su saliente es el estado normal, no un problema.
- Un entrante ya acreditado al saldo a favor del cliente (`whatsapp_balance_entries`) no
  puede marcarse irrelevante: el ledger de abonos ya asume que ese dinero es del negocio.
"""

import pytest

from app.models.whatsapp_operation import WhatsAppOperation
from app.services.whatsapp_balance_service import WhatsAppBalanceService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def _attention_ids(service) -> set[int]:
    page = service.list_payments_page("incoming", attention="ATTENTION", limit=200)
    return {item["id"] for item in page["items"]}


# --------------------------------------------------------------------------- clasificación

def test_marking_incoming_irrelevant_clears_it_even_without_amount(service, db):
    """Mismo caso #4691 que el saliente, del lado entrante."""
    pay = f.incoming(db, None, None)
    service.set_irrelevant("incoming", pay.id, True, "captura repetida")

    assert pay.id not in _attention_ids(service)
    assert service.payments_stats("incoming")["needs_attention"] == 0
    # No se esconde: sigue en la bandeja general y bajo su propia clasificación.
    assert pay.id in {i["id"] for i in service.list_payments_page("incoming")["items"]}
    assert pay.id in {
        i["id"] for i in service.list_payments_page("incoming", out_class="IRRELEVANT")["items"]
    }
    row = next(i for i in service.list_payments_page("incoming")["items"] if i["id"] == pay.id)
    assert row["is_irrelevant"] == 1
    assert row["irrelevant_description"] == "captura repetida"


def test_unmarking_incoming_irrelevant_brings_it_back(service, db):
    pay = f.incoming(db, None, None)
    service.set_irrelevant("incoming", pay.id, True, None)
    service.set_irrelevant("incoming", pay.id, False, None)
    assert pay.id in _attention_ids(service)
    row = next(i for i in service.list_payments_page("incoming")["items"] if i["id"] == pay.id)
    assert row["is_irrelevant"] == 0
    assert row["irrelevant_description"] is None


def test_marking_incoming_irrelevant_unlinks_its_operation(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    # También completa la op para probar que un huérfano COMPLETED se mantiene igual.
    service.set_operation("outgoing", f.outgoing(db, 914.04, "BRL").id, op.uuid,
                           completing_user=operator, complete_outgoing=True)

    res = service.set_irrelevant("incoming", inc.id, True, None, actor=operator)

    assert res["operation_uuid"] is None
    db.refresh(op)
    # El saliente ya vinculado la sostiene: no se pidió decisión de huérfano.
    assert op.id is not None


# --------------------------------------------------------------------------- huérfano

def test_incoming_only_operation_asks_for_orphan_decision(service, db, fund, client, operator):
    """Sin ningún otro comprobante, es el mismo caso que el saliente: hay que decidir."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    with pytest.raises(QuoteServiceError) as exc:
        service.set_irrelevant("incoming", inc.id, True, None, actor=operator)
    assert exc.value.code == "operation_would_be_orphan"

    # KEEP: la operación queda, firmada como aceptada sin comprobante.
    service.set_irrelevant(
        "incoming", inc.id, True, None, actor=operator, orphan_action="KEEP",
    )
    db.refresh(op)
    assert db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op.id).first() is not None
    assert op.no_payments_ack_by_user_id == operator.id


def test_incoming_only_operation_can_delete_on_orphan(service, db, fund, client, operator):
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    service.set_irrelevant(
        "incoming", inc.id, True, None, actor=operator, orphan_action="DELETE_OPERATION",
    )
    assert db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op.id).first() is None


def test_operation_with_only_outgoing_is_not_orphaned_by_its_incoming(service, db, fund, client, operator):
    """
    El caso que distingue al entrante del saliente: una op VIA_PARTNER (o de un par en
    efectivo) nunca tiene entrante y eso es normal. Si YA tenía un saliente que la respalda,
    perder el entrante no debe disparar la pregunta del huérfano — no hace falta decidir nada.
    """
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    service.set_operation("outgoing", f.outgoing(db, 914.04, "BRL").id, op.uuid,
                           completing_user=operator, complete_outgoing=True)

    # No lanza operation_would_be_orphan aunque no se pase orphan_action.
    service.set_irrelevant("incoming", inc.id, True, "reenviado por error", actor=operator)

    db.refresh(op)
    assert db.query(WhatsAppOperation).filter(WhatsAppOperation.id == op.id).first() is not None
    assert op.no_payments_ack_by_user_id is None  # no hubo que aceptar nada


# --------------------------------------------------------------------------- saldo a favor

def test_a_credited_incoming_cannot_be_marked_irrelevant(service, db, client):
    pay = f.incoming(db, 200, "ZELLE")
    WhatsAppBalanceService(db).credit_from_incoming(pay.id, 200, "abono adelantado")

    with pytest.raises(QuoteServiceError) as exc:
        service.set_irrelevant("incoming", pay.id, True, None)
    assert exc.value.code == "payment_is_credited"


def test_an_uncredited_incoming_can_still_be_marked_irrelevant(service, db, client):
    pay = f.incoming(db, 200, "ZELLE")
    service.set_irrelevant("incoming", pay.id, True, "duplicado")
    row = next(i for i in service.list_payments_page("incoming")["items"] if i["id"] == pay.id)
    assert row["is_irrelevant"] == 1
