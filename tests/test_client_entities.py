"""Alta de clientes-entidad: negocios sin teléfono propio en el bot."""

import pytest

from app.models.whatsapp_client import WhatsAppClient
from app.services.client_entity_service import (
    ClientEntityService,
    is_entity_client_phone,
    slugify,
)
from app.services.whatsapp_quote_service import (
    QuoteServiceError,
    is_unassigned_client_phone,
)


def test_slug_strips_accents_and_symbols():
    assert slugify("Bodegón X, C.A.") == "bodegon-x-c-a"


def test_create_entity_builds_synthetic_phone(db):
    entity = ClientEntityService(db).create("Bodegón X")

    assert entity.phone == "entity:bodegon-x"
    assert entity.display_name == "Bodegón X"
    assert is_entity_client_phone(entity.phone) is True
    # Una entidad es un cliente de verdad, no un marcador de "no sabemos quién es".
    assert is_unassigned_client_phone(entity.phone) is False


def test_second_entity_with_same_name_gets_a_suffix(db):
    service = ClientEntityService(db)
    service.create("Bodegón X")
    second = service.create("Bodegón X")

    assert second.phone == "entity:bodegon-x-2"


def test_entity_links_a_group_jid(db):
    entity = ClientEntityService(db).create("Bodegón X", "120363000000000000@g.us")

    assert entity.linked_group_jid == "120363000000000000@g.us"


def test_group_cannot_be_linked_to_two_entities(db):
    service = ClientEntityService(db)
    service.create("Bodegón X", "120363000000000000@g.us")

    with pytest.raises(QuoteServiceError) as exc:
        service.create("Otro negocio", "120363000000000000@g.us")

    assert exc.value.code == "group_already_linked"
    assert exc.value.http_status == 409


def test_entity_name_is_required(db):
    with pytest.raises(QuoteServiceError) as exc:
        ClientEntityService(db).create("   ")

    assert exc.value.code == "invalid_entity_name"
