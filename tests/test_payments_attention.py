"""
Qué sale de la bandeja «Por atender» de los salientes.

Nace del caso real del comprobante #4691: una foto sin monto legible que el operador marcó
como irrelevante y que seguía apareciendo en la lista, porque `amount IS NULL` mandaba sobre
la clasificación y ningún OCR iba a leer un monto de una imagen ya descartada.
"""

import pytest

from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f


@pytest.fixture
def service(db):
    return WhatsAppPaymentService(db)


def _attention_ids(service) -> set[int]:
    page = service.list_payments_page("outgoing", attention="ATTENTION", limit=200)
    return {item["id"] for item in page["items"]}


def test_outgoing_without_amount_needs_attention(service, db):
    pay = f.outgoing(db, None, None)
    assert pay.id in _attention_ids(service)
    assert service.payments_stats("outgoing")["needs_attention"] == 1


def test_marking_irrelevant_clears_it_even_without_amount(service, db):
    """El caso #4691: clasificar es terminal, aunque el OCR nunca haya leído el monto."""
    pay = f.outgoing(db, None, None)
    service.set_irrelevant("outgoing", pay.id, True, None)

    assert pay.id not in _attention_ids(service)
    assert service.payments_stats("outgoing")["needs_attention"] == 0
    # Sigue estando en la bandeja general y bajo su propia clasificación: se saca de lo
    # pendiente, no se esconde.
    assert pay.id in {i["id"] for i in service.list_payments_page("outgoing")["items"]}
    assert pay.id in {
        i["id"] for i in service.list_payments_page("outgoing", out_class="IRRELEVANT")["items"]
    }


def test_marking_personal_clears_it_even_without_amount(service, db):
    pay = f.outgoing(db, None, None)
    service.set_personal_expense(pay.id, True, "almuerzo")
    assert pay.id not in _attention_ids(service)


def test_unmarking_irrelevant_brings_it_back(service, db):
    """Deshacer la clasificación devuelve el comprobante a la bandeja: nada queda oculto."""
    pay = f.outgoing(db, None, None)
    service.set_irrelevant("outgoing", pay.id, True, None)
    service.set_irrelevant("outgoing", pay.id, False, None)
    assert pay.id in _attention_ids(service)


def test_unlinked_outgoing_with_amount_still_needs_attention(service, db):
    """La regla de siempre no cambia: con monto pero sin operación, sigue pendiente."""
    pay = f.outgoing(db, 120.0, "VES")
    assert pay.id in _attention_ids(service)


# ---------------------------------------------------------------------------
# Archivar el comprobante como depósito al fondo
#
# El agujero que apareció en producción: el operador marcaba el comprobante como depósito y la
# fila seguía en «Por atender». Es la misma clase de bug que el #4691 — la decisión existía en
# otra tabla y esta consulta no la miraba.
# ---------------------------------------------------------------------------


@pytest.fixture
def deposits(db):
    from app.services.fund_pending_deposit_service import FundPendingDepositService
    return FundPendingDepositService(db)


@pytest.fixture
def fondo(db, operator):
    from app.models.fund import FundGroup, FundGroupMember
    group = FundGroup(name="Cambios Colombia", currency="COP", is_active=True)
    db.add(group)
    db.flush()
    db.add(FundGroupMember(group_id=group.id, user_id=operator.id, is_fund_manager=True,
                           whatsapp_phone="584249428608"))
    db.flush()
    return group


def _incoming_attention_ids(service) -> set[int]:
    page = service.list_payments_page("incoming", attention="ATTENTION", limit=200)
    return {item["id"] for item in page["items"]}


def test_filing_an_outgoing_as_a_fund_deposit_clears_it(service, db, deposits, fondo, operator):
    """El caso 4928: el dinero no se retiró, se quedó en el fondo. Eso es un destino."""
    pay = f.outgoing(db, 1_000_000, "COP", phone="573123146340", reference="0000021500")
    assert pay.id in _attention_ids(service)

    deposits.create_from_receipt(
        "outgoing", pay.id, fondo.uuid, operator.uuid, created_by_user_id=operator.id,
    )

    assert pay.id not in _attention_ids(service)
    assert service.payments_stats("outgoing")["needs_attention"] == 0
    # No se esconde: sigue en la bandeja general, como cualquier otra clasificación.
    assert pay.id in {i["id"] for i in service.list_payments_page("outgoing")["items"]}


def test_a_rejected_deposit_leaves_the_receipt_in_the_tray(service, db, fondo):
    """
    Rechazar es deshacer la decisión, y el comprobante vuelve a la bandeja.

    Hoy no se llega aquí desde Pagos —señalarlo confirma en el acto— pero la regla se afirma
    igual: si algún día hay un «deshacer» sobre el depósito, la fila tiene que reaparecer sola
    en vez de quedarse escondida con su decisión ya anulada.
    """
    from app.models.fund import (
        FundPendingDeposit,
        FundPendingDepositOrigin,
        FundPendingDepositStatus,
    )

    pay = f.outgoing(db, 1_000_000, "COP", phone="573123146340")
    db.add(FundPendingDeposit(
        group_id=fondo.id, amount=1_000_000, currency="COP",
        status=FundPendingDepositStatus.REJECTED,
        origin=FundPendingDepositOrigin.RECEIPT,
        source_outgoing_payment_id=pay.id,
    ))
    db.flush()

    assert pay.id in _attention_ids(service)


def test_filing_an_incoming_as_a_fund_deposit_clears_it(service, db, deposits, fondo, operator):
    pay = f.incoming(db, 500.0, "ZELLE")
    assert pay.id in _incoming_attention_ids(service)

    deposits.create_from_receipt(
        "incoming", pay.id, fondo.uuid, operator.uuid, created_by_user_id=operator.id,
    )

    assert pay.id not in _incoming_attention_ids(service)


def test_a_duplicate_marker_is_not_a_destination(service, db, deposits, fondo, operator):
    """
    La distinción que importa: `source_incoming_payment_id` en un pendiente detectado por el
    bot dice «esto SE PARECE a ese entrante», no «ese entrante es el depósito». El cliente
    sigue esperando su operación, así que su comprobante NO puede salir de la bandeja.
    """
    from app.models.fund import (
        FundPendingDeposit,
        FundPendingDepositOrigin,
        FundPendingDepositStatus,
    )

    pay = f.incoming(db, 500.0, "ZELLE", reference="0000021500")
    db.add(FundPendingDeposit(
        group_id=fondo.id, amount=500.0, currency="ZELLE", reference="0000021500",
        status=FundPendingDepositStatus.PENDING,
        origin=FundPendingDepositOrigin.GROUP,
        source_incoming_payment_id=pay.id,
    ))
    db.flush()

    assert pay.id in _incoming_attention_ids(service)


def test_the_row_says_it_became_a_fund_deposit(service, db, deposits, fondo, operator):
    """
    Salir de «Por atender» sin decir por qué es el desconcierto de siempre con otra cara: la
    fila tiene que llevar encima la decisión que la sacó.
    """
    pay = f.outgoing(db, 1_000_000, "COP", phone="573123146340")
    deposits.create_from_receipt(
        "outgoing", pay.id, fondo.uuid, operator.uuid, created_by_user_id=operator.id,
    )

    row = next(i for i in service.list_payments_page("outgoing")["items"] if i["id"] == pay.id)
    assert row["fund_deposit"]["status"] == "CONFIRMED"
    assert row["fund_deposit"]["group_name"] == "Cambios Colombia"
    assert row["fund_deposit"]["username"] == operator.username
