"""
La tasa BCV de un momento dado, contra Postgres real.

Cuando un administrador consulta el histórico de un par en la calculadora, el
equivalente en bolívares tiene que salir con el BCV que regía entonces: con el
de hoy, la cotización de un comprobante de ayer queda en otro número.

USD y EUR son series distintas —el euro oficial no se deriva del dólar—, así
que cada consulta filtra por moneda.
"""

from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from app.core.dependencies import get_current_user
from app.database.connection import get_db
from app.main import app
from app.models.bcv_rate import BcvRate
from app.services import bcv_service, valuation


@pytest.fixture(autouse=True)
def clear_bcv_cache():
    """La cache es una global del módulo; sin limpiarla se cuela entre tests."""
    bcv_service._reset_cache_for_test()
    yield
    bcv_service._reset_cache_for_test()


def _bcv(db, rate: float, when: datetime, currency: str = "USD") -> BcvRate:
    row = BcvRate(currency=currency, rate=rate, source="test", fetched_at=when)
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def api(db, operator):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: operator
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_historical_bcv_is_the_one_in_force_then(db):
    now = datetime.now(timezone.utc)
    _bcv(db, 300.0, now - timedelta(days=2))
    _bcv(db, 360.0, now - timedelta(hours=3))

    rate, at = valuation.historical_bcv(db, now - timedelta(days=1))

    assert rate == 300.0
    assert at == now - timedelta(days=2)


def test_euro_and_dollar_do_not_mix(db):
    now = datetime.now(timezone.utc)
    _bcv(db, 300.0, now - timedelta(days=2), currency="USD")
    _bcv(db, 410.0, now - timedelta(hours=3), currency="EUR")

    usd, _ = valuation.historical_bcv(db, now, "USD")
    eur, _ = valuation.historical_bcv(db, now, "EUR")

    # La más reciente de la tabla es la del euro: sin el filtro, el dólar saldría 410.
    assert usd == 300.0
    assert eur == 410.0


def test_before_the_first_record_there_is_nothing(db):
    assert valuation.historical_bcv(db, datetime(2020, 1, 1, tzinfo=timezone.utc)) == (None, None)


def test_endpoint_returns_the_rate_for_the_requested_instant(api, db):
    now = datetime.now(timezone.utc)
    _bcv(db, 300.0, now - timedelta(days=2))
    _bcv(db, 360.0, now - timedelta(hours=3))

    r = api.get("/bcv/historical", params={"at": (now - timedelta(days=1)).isoformat()})

    assert r.status_code == 200
    body = r.json()
    assert body["currency"] == "USD"
    assert body["rate"] == 300.0


def test_endpoint_separates_euro_from_dollar(api, db):
    now = datetime.now(timezone.utc)
    _bcv(db, 300.0, now - timedelta(days=2), currency="USD")
    _bcv(db, 410.0, now - timedelta(days=2), currency="EUR")

    usd = api.get("/bcv/historical", params={"at": now.isoformat(), "currency": "USD"})
    eur = api.get("/bcv/historical", params={"at": now.isoformat(), "currency": "EUR"})

    assert usd.json()["rate"] == 300.0
    assert eur.json()["rate"] == 410.0


def test_endpoint_says_so_when_there_is_no_history(api):
    r = api.get("/bcv/historical", params={"at": "2020-01-01T00:00:00Z"})

    assert r.status_code == 404
    assert "No hay una tasa BCV USD" in r.json()["detail"]


def test_history_requires_a_session(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as client:
            r = client.get("/bcv/historical", params={"at": datetime.now(timezone.utc).isoformat()})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 401


def test_current_bcv_is_the_latest_dollar(api, db):
    now = datetime.now(timezone.utc)
    _bcv(db, 300.0, now - timedelta(days=2), currency="USD")
    _bcv(db, 360.0, now - timedelta(hours=3), currency="USD")
    _bcv(db, 410.0, now - timedelta(hours=1), currency="EUR")

    r = api.get("/bcv")

    assert r.status_code == 200
    assert r.json()["currency"] == "USD"
    assert r.json()["rate"] == 360.0
