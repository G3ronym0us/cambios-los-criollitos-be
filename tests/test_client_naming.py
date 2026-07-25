"""
Quién manda sobre el nombre de un cliente.

El bot manda, en cada cotización, el nombre que el contacto se puso en su propio WhatsApp
(`contact.pushname`). Ese dato lo controla el cliente y cambia cuando a él se le antoja: uno
que se llame «.....» renombraba su ficha en cada cotización, borrando lo que el operador
había escrito.
"""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.services.whatsapp_quote_service import WhatsAppQuoteService


@pytest.fixture
def service(db):
    return WhatsAppQuoteService(db)


def test_the_whatsapp_name_names_a_client_that_had_none(service, db):
    """Estrenar la ficha con el nombre de WhatsApp es mejor que dejarla en el teléfono pelado."""
    client = service.upsert_client("584124640125", "Nelson Azócar")
    assert client.display_name == "Nelson Azócar"


def test_the_whatsapp_name_never_overwrites_the_one_the_operator_chose(service, db):
    """Lo que escribió el operador manda: el cliente no se renombra solo."""
    service.upsert_client("584124640125", "Nelson Azócar")
    client = (
        db.query(WhatsAppClient).filter(WhatsAppClient.phone == "584124640125").first()
    )
    client.display_name = "Nelson"  # el operador lo corrige desde el panel
    db.flush()

    # El cliente se pone «.....» en WhatsApp y vuelve a cotizar.
    again = service.upsert_client("584124640125", ".....")

    assert again.display_name == "Nelson"


def test_quoting_still_refreshes_when_it_was_last_seen(service, db):
    """Que no renombre no significa que no registre el paso del cliente."""
    service.upsert_client("584124640125", "Nelson")
    before = (
        db.query(WhatsAppClient).filter(WhatsAppClient.phone == "584124640125").first()
    ).last_seen_at

    again = service.upsert_client("584124640125", ".....")
    assert again.last_seen_at >= before
