"""
`GET /admin/overview`: el recorte por rol, el fallo aislado por bloque, y el agregado de
deuda con clientes (el único de los tres bloques nuevos que compone sobre varias consultas).

`payments`/`operations`/`me` ya se prueban donde vive su lógica real
(`test_payments_attention.py`, etc.); aquí solo se fija que el ENDPOINT los compone y los
recorta por rol como dice el contrato.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.enums.user_roles import UserRole
from app.models.user import User
from app.services.admin_overview_service import AdminOverviewService
from app.services.client_pending_service import ClientPendingService
from app.services.whatsapp_payment_service import WhatsAppPaymentService
from tests import factories as f

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _user(db, *, role: UserRole, email: str) -> User:
    user = User(
        username=email.split("@")[0], email=email, hashed_password="x",
        is_active=True, is_verified=True, role=role,
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture
def root_user(db) -> User:
    return _user(db, role=UserRole.ROOT, email="root@test.local")


@pytest.fixture
def moderator_user(db) -> User:
    return _user(db, role=UserRole.MODERATOR, email="mod@test.local")


def _get_overview(db, current_user):
    from app.core.dependencies import get_admin_user
    from app.database.connection import get_db
    from app.main import app
    from starlette.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_admin_user] = lambda: current_user
    try:
        with TestClient(app, raise_server_exceptions=False) as api:
            return api.get("/admin/overview")
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------- recorte por rol


def test_root_gets_all_blocks(db, root_user):
    r = _get_overview(db, root_user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "ROOT"
    assert body["errors"] == []
    for key in ("payments", "operations", "me", "alerts", "clients"):
        assert key in body, f"falta {key}"
        assert body[key] is not None


def test_moderator_does_not_get_root_only_blocks(db, moderator_user):
    """`alerts`/`clients` deben estar AUSENTES (ni la clave), no en null."""
    r = _get_overview(db, moderator_user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "MODERATOR"
    assert "alerts" not in body
    assert "clients" not in body
    for key in ("payments", "operations", "me"):
        assert key in body and body[key] is not None


# --------------------------------------------------------------------------- fallo aislado


def test_a_broken_block_goes_null_with_its_name_in_errors_but_the_response_is_still_200(
    db, root_user, monkeypatch
):
    def _boom(self):
        raise RuntimeError("las divergencias no se pudieron leer")

    monkeypatch.setattr(AdminOverviewService, "_alerts_block", _boom)

    r = _get_overview(db, root_user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alerts"] is None
    assert body["errors"] == ["alerts"]
    # El resto sigue al día: un bloque roto no arrastra a los demás.
    assert body["payments"] is not None
    assert body["operations"] is not None
    assert body["me"] is not None
    assert body["clients"] is not None


def test_two_broken_blocks_both_land_in_errors(db, root_user, monkeypatch):
    def _boom(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(AdminOverviewService, "_alerts_block", _boom)
    monkeypatch.setattr(AdminOverviewService, "_clients_block", _boom)

    r = _get_overview(db, root_user)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["alerts"] is None and body["clients"] is None
    assert set(body["errors"]) == {"alerts", "clients"}
    assert body["payments"] is not None


# --------------------------------------------------------------------------- agregado de clientes


class TestClientsAggregate:
    """`ClientPendingService.pending_overview`, que es lo que arma el bloque `clients`."""

    def test_pending_count_totals_and_oldest(self, db, fund, pairs, operator):
        svc = WhatsAppPaymentService(db)

        # Cliente A (ZELLE/VES): debe 100 ZELLE, hace 3 días.
        client_a_phone = "584140001111"
        inc_a = f.incoming(
            db, 100.0, "ZELLE", phone=client_a_phone, created_at=NOW - timedelta(days=3)
        )
        svc.create_operation_from_payment(
            "incoming", inc_a.id, "ZELLE", "VES", 100.0, 78292.0,
            recorded_by_user_id=operator.id,
        )

        # Cliente B (COP/VES): debe 5000 COP, hace 1 día — más reciente que A.
        client_b_phone = "584140002222"
        inc_b = f.incoming(
            db, 5000.0, "COP", phone=client_b_phone, created_at=NOW - timedelta(days=1)
        )
        svc.create_operation_from_payment(
            "incoming", inc_b.id, "COP", "VES", 5000.0, 1227.5,
            recorded_by_user_id=operator.id,
        )
        db.flush()
        db.commit()

        overview = ClientPendingService(db).pending_overview(top_n=3)

        assert overview["pending_count"] == 2

        by_currency = {t["currency"]: t["amount"] for t in overview["totals"]}
        assert by_currency == {"ZELLE": 100.0, "COP": 5000.0}

        # El más viejo (A, hace 3 días) va primero.
        assert len(overview["oldest"]) == 2
        assert overview["oldest"][0]["amount"] == 100.0
        assert overview["oldest"][0]["currency"] == "ZELLE"
        assert overview["oldest"][0]["waiting_days"] >= overview["oldest"][1]["waiting_days"]

    def test_no_debt_is_an_empty_block_not_an_error(self, db, fund, pairs, operator):
        overview = ClientPendingService(db).pending_overview(top_n=3)
        assert overview == {"pending_count": 0, "totals": [], "oldest": []}

    def test_top_n_caps_the_oldest_list(self, db, fund, pairs, operator):
        svc = WhatsAppPaymentService(db)
        for i in range(5):
            phone = f"58414000{i:04d}"
            inc = f.incoming(
                db, 10.0 + i, "ZELLE", phone=phone, created_at=NOW - timedelta(days=i + 1)
            )
            svc.create_operation_from_payment(
                "incoming", inc.id, "ZELLE", "VES", 10.0 + i, (10.0 + i) * 782.92,
                recorded_by_user_id=operator.id,
            )
        db.commit()

        overview = ClientPendingService(db).pending_overview(top_n=3)
        assert overview["pending_count"] == 5
        assert len(overview["oldest"]) == 3
