"""
Por dónde llega un fondo: su grupo de WhatsApp, o el chat directo con su gestor.

El jid del grupo era la única vía y daba por sentado que todo fondo se lleva en un grupo.
«Cambios Colombia» se maneja en el chat directo con Dionis, y cada camino que asumía lo
contrario se rompió por su cuenta: el comprobante de reposición se cotizaba como si fuera de
un cliente, y el reenvío contable del operador quedaba como un saliente fantasma (pago 4951).
Acá se fija la regla, que ahora es una sola para todos esos caminos.
"""

import pytest

from app.models.fund import FundGroup, FundGroupMember
from app.services.fund_channel import resolve_fund_channel
from app.services.whatsapp_quote_service import QuoteServiceError
from tests import factories as f


@pytest.fixture
def colombia(db, operator) -> FundGroup:
    """Un fondo SIN grupo de WhatsApp: se lleva en el chat directo con su gestor."""
    group = FundGroup(name="Cambios Colombia", currency="COP", is_active=True)
    db.add(group)
    db.flush()
    db.add(FundGroupMember(
        group_id=group.id, user_id=operator.id, is_fund_manager=True,
        whatsapp_phone="573123146340",
    ))
    db.flush()
    return group


def test_resolves_a_fund_by_its_group_jid(db, fund):
    fund.whatsapp_group_jid = "120363@g.us"
    db.flush()
    assert resolve_fund_channel(db, group_jid="120363@g.us").id == fund.id


def test_resolves_a_fund_by_its_manager_phone_when_there_is_no_group(db, colombia):
    assert resolve_fund_channel(db, manager_phone="573123146340").id == colombia.id


def test_resolves_a_fund_by_uuid(db, colombia):
    assert resolve_fund_channel(db, group_uuid=colombia.uuid).id == colombia.id


def test_a_phone_that_manages_no_fund_is_not_a_channel(db, colombia):
    with pytest.raises(QuoteServiceError) as exc:
        resolve_fund_channel(db, manager_phone="584249154928")
    assert exc.value.code == "fund_group_not_found"


def test_a_member_who_is_not_the_manager_is_not_a_channel(db, colombia, operator):
    colombia.members[0].is_fund_manager = False
    db.flush()
    with pytest.raises(QuoteServiceError) as exc:
        resolve_fund_channel(db, manager_phone="573123146340")
    assert exc.value.code == "fund_group_not_found"


def test_managing_two_funds_is_rejected_instead_of_guessed(db, colombia, fund):
    """Adivinar movería capital al fondo equivocado."""
    # El mismo teléfono gestionando los dos fondos (el operador gestiona varios: es normal).
    fund.members[0].whatsapp_phone = "573123146340"
    fund.members[0].is_fund_manager = True
    db.flush()
    with pytest.raises(QuoteServiceError) as exc:
        resolve_fund_channel(db, manager_phone="573123146340")
    assert exc.value.code == "ambiguous_fund_group"
    assert exc.value.http_status == 409


# ---------------------------------------------------------------------------
# El reenvío contable que llega por el chat del gestor (pago 4951)
# ---------------------------------------------------------------------------


def test_forwarding_an_incoming_through_the_manager_chat_books_it_in_the_fund(db, colombia, pairs, operator):
    """
    El caso real: el cliente manda su comprobante de Bancolombia, el operador lo reenvía al
    chat de Dionis —que ES el fondo de Colombia— y le paga en Bs. Antes, resolver el fondo
    sólo por jid hacía fallar el marcado y el comprobante quedaba como saliente fantasma.
    """
    from app.services.whatsapp_payment_service import WhatsAppPaymentService

    pago = f.incoming(db, 114000, currency="COP", phone="584249154928", reference="1248040679")
    service = WhatsAppPaymentService(db)
    op = f.create_op_from_payment(
        service, "incoming", pago, frm="COP", to="VES",
        from_amount=114000, to_amount=27987, recorded_by=operator.id,
    )

    service.mark_incoming_forwarded_to_group(pago.id, manager_phone="573123146340")

    db.refresh(pago)
    assert pago.operation.fund_group_id == colombia.id, "el entrante quedó contabilizado en el fondo"
    assert op["uuid"] is not None
