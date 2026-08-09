"""
El CRUD de la libreta visto desde el panel, y qué llega cuando algo revienta.

El caso que lo motivó: guardar una cuenta editada con los datos de otra ya guardada
chocaba con `uq_client_account_payment_info`, salía como 500 sin cabeceras CORS y el panel
lo mostraba como "Error de conexión al servidor" — un mensaje con el que no se puede
depurar nada.
"""

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from app.core.dependencies import get_current_user, get_moderator_user
from app.database.connection import get_db
from app.main import app
from app.repositories.whatsapp_client_account_repository import WhatsAppClientAccountRepository

ELSI = "01020123300000211200\nV24774193\nElsi Carolina Hernández Soto"
KELLY = "01340866100001310098\nV26640340\nKelly Zitman"
ORIGIN = {"Origin": "https://panel.cambiosloscriollitos.com"}


@pytest.fixture
def api(db, operator):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_moderator_user] = lambda: operator
    app.dependency_overrides[get_current_user] = lambda: operator
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def accounts(db, client):
    repo = WhatsAppClientAccountRepository(db)
    return (
        repo.create(client_id=client.id, alias="elsi carolina hernández soto",
                    payment_info=ELSI, currency="VES", source="MESSAGE"),
        repo.create(client_id=client.id, alias="kelly zitman",
                    payment_info=KELLY, currency="VES", source="MESSAGE"),
    )


def test_confirming_an_account(api, accounts):
    elsi, _ = accounts

    r = api.patch(f"/clients/accounts/{elsi.uuid}", json={"is_confirmed": True}, headers=ORIGIN)

    assert r.status_code == 200
    assert r.json()["is_confirmed"] is True


def test_saving_the_data_of_another_account_is_a_conflict(api, accounts):
    elsi, kelly = accounts

    r = api.patch(f"/clients/accounts/{elsi.uuid}", json={"payment_info": KELLY}, headers=ORIGIN)

    assert r.status_code == 409
    assert r.json()["detail"] == "El cliente ya tiene esa cuenta guardada"
    # El aviso llega con CORS: el panel puede leerlo y mostrarlo tal cual.
    assert r.headers["access-control-allow-origin"] == "*"


def test_saving_its_own_data_unchanged_is_fine(api, accounts):
    elsi, _ = accounts

    r = api.patch(f"/clients/accounts/{elsi.uuid}", json={"payment_info": ELSI}, headers=ORIGIN)

    assert r.status_code == 200


def test_an_unhandled_error_arrives_as_json_with_cors(api):
    """
    Sin esto el navegador bloquea la respuesta del 500 por falta de CORS y el `fetch` del
    panel falla como si el servidor no existiera.
    """
    @app.get("/_boom_")
    def boom():
        raise RuntimeError("algo se rompió adentro")

    try:
        r = api.get("/_boom_", headers=ORIGIN)

        assert r.status_code == 500
        assert r.json()["detail"] == "Error interno del servidor"
        assert r.headers["access-control-allow-origin"] == "*"
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", None) != "/_boom_"
        ]


def test_listing_the_accounts_of_a_client(api, client, accounts):
    r = api.get(f"/clients/{client.uuid}/accounts", headers=ORIGIN)

    assert r.status_code == 200
    assert {a["payment_info"] for a in r.json()["items"]} == {ELSI, KELLY}
    assert r.headers["access-control-allow-origin"] == "*"
