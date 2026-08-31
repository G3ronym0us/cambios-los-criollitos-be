"""
Transferir un comprobante a otro cliente.

El caso: el comprobante entra a nombre de quien lo mandó, pero el dinero es de otro. Lo que se
prueba aquí es sobre todo lo que NO tiene que pasar — el pago no se duplica, no cambia de
fecha, no pierde el teléfono del que mandó el dinero, y no se puede mover si ya movió caja.
"""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.models.whatsapp_operation import WhatsAppOperationStatus
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


@pytest.fixture
def destination(db) -> WhatsAppClient:
    """La esposa: la dueña real del dinero que mandó el marido."""
    row = WhatsAppClient(phone="584125510388", display_name="Marielys C. Rondon")
    db.add(row)
    db.flush()
    return row


def _transfer(service, payment, destination, operator=None, reason="THIRD_PARTY", note=None):
    return service.transfer_client(
        "incoming", payment.id, destination.uuid, reason, note, actor=operator
    )


def test_transfer_moves_the_owner_without_touching_the_receipt(
    service, db, client, destination, operator
):
    pay = f.incoming(db, 220.0)
    original_created_at = pay.created_at

    out = _transfer(service, pay, destination, operator, note="El esposo mandó el Zelle")

    # Cambia de dueño...
    assert out["client_uuid"] == str(destination.uuid)
    assert out["client_name"] == "Marielys C. Rondon"
    # ...pero es el MISMO pago: mismo id, misma fecha, mismo teléfono de origen.
    assert out["id"] == pay.id
    db.refresh(pay)
    assert pay.created_at == original_created_at
    assert pay.client_phone == client.phone


def test_the_trail_says_where_it_came_from(service, db, client, destination, operator):
    pay = f.incoming(db, 220.0)
    out = _transfer(service, pay, destination, operator, note="La operación es de ella")

    trail = out["transfer"]
    assert trail["from_client_name"] == client.display_name
    assert trail["from_client_uuid"] == str(client.uuid)
    assert trail["reason"] == "THIRD_PARTY"
    assert trail["note"] == "La operación es de ella"
    assert trail["transferred_by"] == operator.username
    assert trail["count"] == 1


def test_untransferred_payment_has_no_trail(service, db, client):
    pay = f.incoming(db, 220.0)
    assert service._with_name(pay)["transfer"] is None


def test_origin_stays_searchable_and_destination_finds_it_too(
    service, db, client, destination, operator
):
    """
    El punto entero del override: buscar por el nombre del origen sigue encontrando el pago,
    para que quien lo mandó no lo dé por perdido. Y el destino también, porque es suyo.
    """
    pay = f.incoming(db, 220.0)
    _transfer(service, pay, destination, operator)

    def ids(search):
        return {i["id"] for i in service.list_payments_page("incoming", search=search)["items"]}

    assert pay.id in ids(client.display_name)      # Naldin, el origen
    assert pay.id in ids("Marielys")               # el destino
    assert pay.id in ids(client.phone)             # el teléfono del comprobante


def test_listing_shows_the_new_owner_and_its_trail(service, db, client, destination, operator):
    pay = f.incoming(db, 220.0)
    _transfer(service, pay, destination, operator)

    item = next(
        i for i in service.list_payments_page("incoming")["items"] if i["id"] == pay.id
    )
    assert item["client_uuid"] == str(destination.uuid)
    assert item["transfer"]["count"] == 1
    assert item["transfer"]["from_client_name"] == "Naldin"


def test_transferring_twice_keeps_the_first_origin(service, db, client, destination, operator):
    """Se puede mover varias veces; la cabecera sigue diciendo de dónde salió el dinero."""
    third = WhatsAppClient(phone="584140000000", display_name="Jose Carrizo")
    db.add(third)
    db.flush()

    pay = f.incoming(db, 220.0)
    _transfer(service, pay, destination, operator)
    out = service.transfer_client(
        "incoming", pay.id, third.uuid, "DUPLICATE_CLIENT", None, actor=operator
    )

    assert out["client_uuid"] == str(third.uuid)
    assert out["transfer"]["from_client_name"] == client.display_name  # el PRIMER origen
    assert out["transfer"]["reason"] == "DUPLICATE_CLIENT"             # el ÚLTIMO motivo
    assert out["transfer"]["count"] == 2


def test_transfer_unlinks_the_operation_and_leaves_it_waiting(
    service, db, client, destination, operator, pairs, fund
):
    """
    La operación vieja no se muda con el pago: queda sin comprobante y esperando fondos. Y no
    se marca como «asumida sin pagos» — esa alarma tiene que seguir encendida.
    """
    pay = f.incoming(db, 220.0)
    op = f.create_op_from_payment(
        service, "incoming", pay,
        frm="ZELLE", to="BRL", from_amount=220.0, to_amount=1005.44,
        recorded_by=operator.id,
    )
    db.refresh(pay)
    assert pay.whatsapp_operation_id is not None

    out = _transfer(service, pay, destination, operator, reason="BOT_MISMATCH")

    db.refresh(pay)
    assert pay.whatsapp_operation_id is None
    assert out["operation_uuid"] is None

    from app.models.whatsapp_operation import WhatsAppOperation
    row = db.query(WhatsAppOperation).filter(WhatsAppOperation.uuid == op["uuid"]).first()
    assert row.status != WhatsAppOperationStatus.COMPLETED
    assert row.no_payments_ack_at is None  # sigue reclamando fondos, no asumida


def test_cannot_transfer_a_reconciled_payment(
    service, db, client, destination, operator, pairs, fund
):
    pay = f.incoming(db, 220.0)
    f.create_op_from_payment(
        service, "incoming", pay,
        frm="ZELLE", to="BRL", from_amount=220.0, to_amount=1005.44,
        recorded_by=operator.id,
    )
    db.refresh(pay)
    pay.operation.status = WhatsAppOperationStatus.COMPLETED
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        _transfer(service, pay, destination, operator)
    assert exc.value.http_status == 409
    db.refresh(pay)
    assert pay.owner_client_id is None  # no se movió nada


def test_cannot_transfer_what_is_already_credited_to_balance(
    service, db, client, destination, operator
):
    from app.services.whatsapp_balance_service import WhatsAppBalanceService

    pay = f.incoming(db, 220.0, currency="ZELLE")
    WhatsAppBalanceService(db).credit_from_incoming(
        pay.id, 220.0, None, created_by_user_id=operator.id
    )

    with pytest.raises(QuoteServiceError) as exc:
        _transfer(service, pay, destination, operator)
    assert exc.value.http_status == 409


def test_cannot_transfer_to_the_same_client(service, db, client, operator):
    pay = f.incoming(db, 220.0)
    with pytest.raises(QuoteServiceError) as exc:
        service.transfer_client("incoming", pay.id, client.uuid, "THIRD_PARTY", None, operator)
    assert exc.value.http_status == 422


def test_unknown_client_and_bad_reason_are_rejected(service, db, destination, operator):
    import uuid as _uuid

    pay = f.incoming(db, 220.0)
    with pytest.raises(QuoteServiceError) as exc:
        service.transfer_client(
            "incoming", pay.id, _uuid.uuid4(), "THIRD_PARTY", None, operator
        )
    assert exc.value.http_status == 404

    with pytest.raises(QuoteServiceError) as exc:
        _transfer(service, pay, destination, operator, reason="PORQUE_SI")
    assert exc.value.http_status == 422


def test_timeline_tells_the_move_and_the_unlink(
    service, db, client, destination, operator, pairs, fund
):
    pay = f.incoming(db, 220.0)
    f.create_op_from_payment(
        service, "incoming", pay,
        frm="ZELLE", to="BRL", from_amount=220.0, to_amount=1005.44,
        recorded_by=operator.id,
    )
    _transfer(service, pay, destination, operator, note="El esposo mandó el Zelle")

    items = service.payment_timeline("incoming", pay.id)["items"]
    kinds = [i["kind"] for i in items]
    assert "TRANSFER" in kinds and "UNLINK" in kinds

    move = next(i for i in items if i["kind"] == "TRANSFER")
    assert "Naldin" in move["detail"] and "Marielys" in move["detail"]
    assert "pagó un tercero" in move["detail"]
    assert "El esposo mandó el Zelle" in move["detail"]
    assert move["actor"] == operator.username

    unlink = next(i for i in items if i["kind"] == "UNLINK")
    assert unlink["actor"] is None  # automático

    # Lo más reciente primero, y la recepción del comprobante al final.
    assert items[-1]["kind"] == "OTHER"


def test_timeline_of_an_untouched_payment_only_has_its_arrival(service, db, client):
    pay = f.incoming(db, 220.0)
    items = service.payment_timeline("incoming", pay.id)["items"]
    assert [i["kind"] for i in items] == ["OTHER"]


# --------------------------------------------------------------------- salientes
#
# El comprobante de SALIDA también se pega al cliente equivocado, y ahí el error escuece más:
# es dinero que ya salió. Las reglas son las mismas salvo una propia — el préstamo.


def test_outgoing_receipt_can_be_transferred_too(service, db, client, destination, operator):
    pay = f.outgoing(db, 914.04, "BRL")

    out = service.transfer_client(
        "outgoing", pay.id, destination.uuid, "BOT_MISMATCH", None, actor=operator
    )

    assert out["client_uuid"] == str(destination.uuid)
    assert out["transfer"]["from_client_name"] == client.display_name
    db.refresh(pay)
    assert pay.client_phone == client.phone  # el comprobante no se toca


def test_outgoing_listing_and_search_behave_like_the_incoming_one(
    service, db, client, destination, operator
):
    pay = f.outgoing(db, 914.04, "BRL")
    service.transfer_client(
        "outgoing", pay.id, destination.uuid, "THIRD_PARTY", None, actor=operator
    )

    def ids(search=None):
        return {
            i["id"] for i in service.list_payments_page("outgoing", search=search)["items"]
        }

    assert pay.id in ids(client.display_name)  # el origen sigue encontrándose
    assert pay.id in ids("Marielys")           # y el destino también

    item = next(i for i in service.list_payments_page("outgoing")["items"] if i["id"] == pay.id)
    assert item["transfer"]["count"] == 1


def test_cannot_transfer_an_outgoing_registered_as_a_loan(
    service, db, client, destination, operator
):
    """
    La deuda quedó a nombre del cliente de origen, con su valuación y sus abonos. Mudar el
    comprobante la dejaría con quien no la tiene.
    """
    from datetime import datetime, timezone
    from app.models.client_loan import (
        ClientLoan,
        ClientLoanPreferredValue,
        ClientLoanStatus,
    )

    pay = f.outgoing(db, 5000, "VES", phone=client.phone)
    # El préstamo se inserta directo: lo que se prueba es el candado, no la valuación, y
    # montarla entera arrastraría las tasas VES/USDT y BCV sin añadir nada al caso.
    db.add(ClientLoan(
        client_id=client.id,
        outgoing_payment_id=pay.id,
        fiat_amount=5000, fiat_currency="VES",
        usdt_amount=10, usdt_rate=500,
        valuation_at=datetime.now(timezone.utc),
        preferred_value=ClientLoanPreferredValue.FIAT,
        status=ClientLoanStatus.OPEN,
    ))
    db.flush()

    with pytest.raises(QuoteServiceError) as exc:
        service.transfer_client(
            "outgoing", pay.id, destination.uuid, "THIRD_PARTY", None, actor=operator
        )
    assert exc.value.http_status == 409
    db.refresh(pay)
    assert pay.owner_client_id is None


def test_outgoing_timeline_reads_the_move(service, db, client, destination, operator):
    pay = f.outgoing(db, 914.04, "BRL")
    service.transfer_client(
        "outgoing", pay.id, destination.uuid, "DUPLICATE_CLIENT", "misma persona", actor=operator
    )

    items = service.payment_timeline("outgoing", pay.id)["items"]
    move = next(i for i in items if i["kind"] == "TRANSFER")
    assert "cliente duplicado" in move["detail"]
    assert "misma persona" in move["detail"]
