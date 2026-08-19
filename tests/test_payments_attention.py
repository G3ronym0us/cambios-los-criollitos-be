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
    service.set_irrelevant(pay.id, True, None)

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
    service.set_irrelevant(pay.id, True, None)
    service.set_irrelevant(pay.id, False, None)
    assert pay.id in _attention_ids(service)


def test_unlinked_outgoing_with_amount_still_needs_attention(service, db):
    """La regla de siempre no cambia: con monto pero sin operación, sigue pendiente."""
    pay = f.outgoing(db, 120.0, "VES")
    assert pay.id in _attention_ids(service)
