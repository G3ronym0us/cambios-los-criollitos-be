"""
Regression for H-4.2: rate-divergence surveillance ("vigilancia") is ROOT-only policy,
already enforced by GET /admin/overview (the "alerts" block is absent for MODERATOR).

`app/routers/notifications.py` exposed the same data (`RateAlertRepository`) through
`GET /notifications/alerts`, `GET /notifications/stream` and the web-push endpoints, but
those were gated with `get_moderator_user` instead of `get_root_user` — a MODERATOR could
read and acknowledge rate divergence alerts through this second door, bypassing the policy
enforced in the overview endpoint.

This test locks the fix: a MODERATOR gets 403 on every alert/push endpoint, ROOT gets past
the auth layer (200, or an expected non-403 failure for endpoints that need further setup
like VAPID keys).
"""

from datetime import datetime, timezone

import pytest

from app.enums.user_roles import UserRole
from app.models.user import User


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
    return _user(db, role=UserRole.ROOT, email="root-notif@test.local")


@pytest.fixture
def moderator_user(db) -> User:
    return _user(db, role=UserRole.MODERATOR, email="mod-notif@test.local")


def _client_as(db, current_user):
    from app.database.connection import get_db
    from app.main import app
    from starlette.testclient import TestClient

    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app, raise_server_exceptions=False)


def _override_user(db, current_user):
    """Overrides `get_current_user`/`get_current_active_user` so `require_role` (which the
    dependency chain still runs) evaluates the real role of `current_user` instead of a
    fixed dependency override — this is what actually exercises the fix under test."""
    from app.core.dependencies import get_current_user, get_current_active_user
    from app.database.connection import get_db
    from app.main import app

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: current_user
    app.dependency_overrides[get_current_active_user] = lambda: current_user


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/notifications/alerts"),
        ("POST", "/notifications/alerts/00000000-0000-0000-0000-000000000000/acknowledge"),
        ("GET", "/notifications/push/public-key"),
        ("POST", "/notifications/push/test"),
    ],
)
def test_moderator_is_forbidden_from_rate_alert_surveillance(db, moderator_user, method, path):
    _override_user(db, moderator_user)
    try:
        from starlette.testclient import TestClient
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as api:
            r = api.request(method, path)
        assert r.status_code == 403, f"{method} {path} -> {r.status_code}: {r.text}"
    finally:
        app.dependency_overrides.clear()


def test_root_passes_the_role_gate_on_rate_alerts(db, root_user):
    _override_user(db, root_user)
    try:
        from starlette.testclient import TestClient
        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as api:
            r = api.get("/notifications/alerts")
        # ROOT must clear the role gate (never a 403). The DB has no alerts seeded here,
        # so 200 with an empty list is exactly what "passed the gate" looks like.
        assert r.status_code == 200, r.text
        assert r.json()["alerts"] == []
    finally:
        app.dependency_overrides.clear()
