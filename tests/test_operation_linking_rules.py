"""
Reglas de vínculo de una operación con sus comprobantes, contra Postgres real. Reemplazan a
los tests mock que se rompían con cada refactor y algunos de los cuales probaban comportamiento
que el modelo por valor cambió a propósito (ej. «una op no admite un segundo saliente»).
"""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperation, WhatsAppOperationStatus
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import WhatsAppQuoteService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _op(db, uuid):
    return db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == str(uuid)).first()


def test_operation_accepts_several_payouts(service, db, fund, client, operator):
    """El modelo por valor SÍ admite varios salientes por operación (antes se rechazaba)."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    service.set_operation("outgoing", f.outgoing(db, 914.04, "BRL").id, op.uuid,
                          completing_user=operator, complete_outgoing=True)
    # El segundo saliente ya no da 409: se contabiliza como otra parte del valor.
    res = service.set_operation("outgoing", f.outgoing(db, 15658.4, "VES").id, op.uuid,
                                completing_user=operator, complete_outgoing=True)
    assert str(res["operation_uuid"]) == str(op.uuid)


def test_incoming_operation_stays_pending_until_a_payout(service, db, fund, client, operator):
    """Un entrante inicia la op pero no la completa: falta entregar el dinero al cliente."""
    inc = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=220, to_amount=1005.44,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.status == WhatsAppOperationStatus.PENDING and op.transaction_id is not None


def test_operation_from_group_receipt_uses_anonymous_client(service, db, fund, pairs, operator):
    """Un comprobante reenviado al grupo no pone al grupo como cliente: queda anónimo."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    pay = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", pay, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.client.phone.startswith("anon:")


def test_linking_outgoing_adopts_the_real_client_of_an_anonymous_op(service, db, fund, pairs, operator):
    """Al vincular el saliente real a una op anónima de grupo, adopta ese teléfono como cliente."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    inc = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    assert op.client.phone.startswith("anon:")

    pix = f.outgoing(db, 914.04, "BRL", phone="584127777777")
    service.set_operation("outgoing", pix.id, op.uuid, completing_user=operator, complete_outgoing=True)
    db.refresh(op)
    assert op.client.phone == "584127777777"


def test_operation_inherits_fund_group_of_the_payment_when_omitted(service, db, fund, pairs, operator):
    """Crear la op sin indicar fondo hereda el del comprobante reenviado al grupo."""
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    pay = f.incoming(db, 200, "ZELLE", phone="120363@g.us")
    op = _op(db, service.create_operation_from_payment(
        "incoming", pay.id, "ZELLE", "BRL", 200, 914.04,
        exchange_user_uuid=operator.uuid, recorded_by_user_id=operator.id,
    )["uuid"])
    assert op.fund_group_id == fund.id


def test_convert_outgoing_to_incoming_keeps_operation_and_date(service, db, fund, client, operator):
    """Mover un saliente a la bandeja de entrantes conserva su operación y su fecha."""
    from datetime import datetime, timezone
    when = datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
    inc = f.incoming(db, 200, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", inc, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    out = f.outgoing(db, 500, "VES", created_at=when, whatsapp_operation_id=op.id)

    res = service.convert_outgoing_to_incoming(out.id)
    assert str(res["operation_uuid"]) == str(op.uuid)
    assert res["created_at"].replace(tzinfo=timezone.utc) == when


# ---------------------------------------------------------------------------
# Reparto implícito: el vínculo directo ya asigna el pago
#
# El FK es la relación primaria y vale por sí sola (`_sync_primary_operation`); el reparto
# explícito solo hace falta cuando UN pago respalda VARIAS ops. Leer únicamente la tabla de
# reparto hacía que todo lo que crea el bot —FK sin reparto— se anunciara como "sin asignar".
# ---------------------------------------------------------------------------


def _bot_creates_incoming(service, db, op_uuid, amount, currency="ZELLE", phone="13174961478"):
    """El camino del bot: POST /whatsapp/payments/incoming con la op ya resuelta."""
    from app.schemas.whatsapp import WhatsAppPaymentCreate

    created = service.create_payment(
        "incoming",
        WhatsAppPaymentCreate(
            client_phone=phone,
            raw_text="comprobante",
            operation_uuid=op_uuid,
            amount=amount,
            currency=currency,
        ),
    )
    db.flush()
    return created


def _listed(service, payment_id):
    return next(p for p in service.list_payments_page("incoming", limit=100)["items"]
                if p["id"] == payment_id)


def test_a_directly_linked_payment_is_not_reported_as_unassigned(service, db, fund, client, operator):
    """El pago 161 de producción: bien vinculado por el bot y aun así decía '60 sin asignar'."""
    seed = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", seed, frm="ZELLE", to="BRL", from_amount=60, to_amount=274.21,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    # La op nace con su comprobante; se desvincula para dejarla libre y que el bot le cuelgue
    # el suyo, que es la secuencia real (cotización primero, comprobante después).
    service.set_operation("incoming", seed.id, None, orphan_action="KEEP")

    created = _bot_creates_incoming(service, db, op.uuid, 60)
    item = _listed(service, created["id"])

    assert item["allocated_amount"] == 60
    assert item["unassigned_amount"] == 0
    # No es una fila de reparto: el contador sigue en cero.
    assert item["allocations_count"] == 0


def test_a_payment_larger_than_its_operation_still_shows_the_real_surplus(
    service, db, fund, client, operator
):
    """220 respaldando una op de 200 → sobran 20, que es el aviso que sí hay que dar."""
    seed = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", seed, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    service.set_operation("incoming", seed.id, None, orphan_action="KEEP")

    created = _bot_creates_incoming(service, db, op.uuid, 220)
    item = _listed(service, created["id"])

    assert item["allocated_amount"] == 200
    assert item["unassigned_amount"] == 20


def test_a_payment_without_operation_is_fully_unassigned(service, db, fund, client, operator):
    """El de Dionis: 100 ZELLE sin operación ninguna. Sigue estando sin asignar entero."""
    created = _bot_creates_incoming(service, db, None, 100)
    item = _listed(service, created["id"])

    assert item["allocated_amount"] == 0
    assert item["unassigned_amount"] == 100


def test_the_allocation_panel_agrees_with_the_list(service, db, fund, client, operator):
    """Las dos pantallas leían la misma relación y decían cosas distintas."""
    seed = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", seed, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])
    service.set_operation("incoming", seed.id, None, orphan_action="KEEP")

    created = _bot_creates_incoming(service, db, op.uuid, 220)
    summary = service.allocation_summary(created["id"])

    assert summary["assigned"] == 200
    assert summary["unassigned"] == 20
    assert len(summary["allocations"]) == 1
    # Sin fila detrás todavía: se materializa si el operador guarda desde el panel.
    assert summary["allocations"][0]["uuid"] is None
    assert str(summary["allocations"][0]["operation_uuid"]) == str(op.uuid)


def test_an_explicit_split_still_wins_over_the_implicit_one(service, db, fund, client, operator):
    """Con reparto explícito manda el reparto: el implícito solo cubre su ausencia."""
    seed = f.incoming(db, 220, "ZELLE")
    op = _op(db, f.create_op_from_payment(
        service, "incoming", seed, frm="ZELLE", to="BRL", from_amount=200, to_amount=914.04,
        fund_uuid=fund.uuid, user_uuid=operator.uuid, recorded_by=operator.id)["uuid"])

    item = _listed(service, seed.id)
    assert item["allocations_count"] == 1
    assert item["allocated_amount"] == 200
    assert item["unassigned_amount"] == 20
