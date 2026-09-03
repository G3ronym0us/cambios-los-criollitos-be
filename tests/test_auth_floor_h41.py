"""
Regression for H-4.1 (`/tmp/.../hallazgos/04-autorizacion.md`), plus the deeper bug found
while implementing it.

Two separate things are locked down here, with REAL minted JWTs sent through the ASGI app
via TestClient (not dependency overrides) — the closest thing to the live "acuña un token
y llama al endpoint" verification the task calls for:

1. `require_role` bug (predates this branch — commit ace2a098, 2025-07-02): it compared
   `UserRole.value` (the strings "USER"/"MODERATOR"/"ROOT") instead of `.level` (the
   intended integer hierarchy). Alphabetically "USER" is not less than "MODERATOR" or
   "ROOT", so `get_moderator_user`/`get_root_user` never actually blocked a USER — every
   dependency upgrade below (and every pre-existing get_moderator_user/get_root_user gate
   in the app) was a no-op against a USER-role caller. Fixed in app/core/dependencies.py
   to compare `.level`.

2. H-4.1 itself: `POST /auth/register` is now ROOT-only (no legitimate anonymous caller —
   staff creation goes through `POST /auth/admin/create-user`), and a representative
   endpoint from each business router that used to accept a bare `get_current_user` now
   requires `get_moderator_user`. A self-registered, never-verified USER — exactly the
   attacker shape from the live reproduction in the report — must get 401/403 everywhere
   it used to get 200.
"""

import pytest
from starlette.testclient import TestClient

from app.core.security import create_access_token
from app.database.connection import get_db
from app.enums.user_roles import UserRole
from app.main import app
from app.models.user import User


def _user(db, *, role: UserRole, email: str, is_verified: bool = True) -> User:
    user = User(
        username=email.split("@")[0], email=email, hashed_password="x",
        is_active=True, is_verified=is_verified, role=role,
    )
    db.add(user)
    db.flush()
    return user


def _token(email: str) -> str:
    return create_access_token({"sub": email})


@pytest.fixture
def api(db):
    """TestClient wired to the test DB, with NO auth dependency overrides — every request
    goes through the real get_current_user -> get_current_active_user -> require_role
    chain, decoding a real JWT and reading the real role from the DB row."""
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield client
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------------------
# 1. The require_role level-vs-value bug
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "role,expect_blocked",
    [(UserRole.USER, True), (UserRole.MODERATOR, False), (UserRole.ROOT, False)],
)
def test_get_moderator_user_gate_by_role(db, api, role, expect_blocked):
    """A representative get_moderator_user-gated route: POST /clients — must reject USER,
    accept MODERATOR/ROOT past the role gate (a downstream validation error is fine, a 401
    or 403 is not)."""
    user = _user(db, role=role, email=f"floor-mod-{role.value.lower()}@test.local")
    token = _token(user.email)
    r = api.post(
        "/clients",
        json={"phone": "10000000000"},
        headers={"Authorization": f"Bearer {token}"},
    )
    if expect_blocked:
        assert r.status_code == 403, f"{role.name} -> {r.status_code}: {r.text}"
    else:
        assert r.status_code != 403, f"{role.name} -> {r.status_code}: {r.text}"
        assert r.status_code != 401, f"{role.name} -> {r.status_code}: {r.text}"


@pytest.mark.parametrize(
    "role,expect_blocked",
    [(UserRole.USER, True), (UserRole.MODERATOR, True), (UserRole.ROOT, False)],
)
def test_get_root_user_gate_by_role(db, api, role, expect_blocked):
    """A representative get_root_user-gated route: GET /currency-pairs/stats."""
    user = _user(db, role=role, email=f"floor-root-{role.value.lower()}@test.local")
    token = _token(user.email)
    r = api.get("/currency-pairs/stats", headers={"Authorization": f"Bearer {token}"})
    if expect_blocked:
        assert r.status_code == 403, f"{role.name} -> {r.status_code}: {r.text}"
    else:
        assert r.status_code != 403, f"{role.name} -> {r.status_code}: {r.text}"
        assert r.status_code != 401, f"{role.name} -> {r.status_code}: {r.text}"


# --------------------------------------------------------------------------------------
# 2. H-4.1: POST /auth/register is now ROOT-only
# --------------------------------------------------------------------------------------


def test_register_requires_root(db, api):
    """No token at all -> the endpoint must never have been reachable anonymously."""
    r = api.post(
        "/auth/register",
        json={"username": "probe_new", "email": "probe-new@test.local", "password": "Probe123456"},
    )
    assert r.status_code == 401, r.text


def test_register_rejects_non_root(db, api):
    moderator = _user(db, role=UserRole.MODERATOR, email="mod-register@test.local")
    token = _token(moderator.email)
    r = api.post(
        "/auth/register",
        json={"username": "probe_new2", "email": "probe-new2@test.local", "password": "Probe123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 403, r.text


def test_register_allows_root(db, api):
    root = _user(db, role=UserRole.ROOT, email="root-register@test.local")
    token = _token(root.email)
    # UserRegister validates the email format strictly (email-validator rejects
    # reserved TLDs like .local), so this one request needs a real-looking domain —
    # unlike the User rows built directly via the ORM elsewhere in this file.
    r = api.post(
        "/auth/register",
        json={"username": "probe_new3", "email": "probe-new3@example.com", "password": "Probe123456"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "USER"


# --------------------------------------------------------------------------------------
# 3. H-4.1: an unverified, self-registered USER — the exact live reproduction from the
#    report — is now shut out of the business routers it used to read/write freely.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/clients"),
        ("GET", "/clients/00000000-0000-0000-0000-000000000000"),
        ("GET", "/funds/groups"),
        ("GET", "/operations"),
        ("GET", "/analyses"),
        ("GET", "/payments/incoming"),
        ("GET", "/transactions"),
        ("GET", "/commission-configs/pairs"),
        # write paths: the report noted these returned 404 (past the authz layer, into the
        # 404-for-a-fake-id branch) instead of 401/403 — that's the bug.
        ("PATCH", "/operations/00000000-0000-0000-0000-000000000000"),
        ("POST", "/payments/incoming/999999999/credit-balance"),
        ("POST", "/clients/00000000-0000-0000-0000-000000000000/loans"),
    ],
)
def test_unverified_self_registered_user_is_shut_out(db, api, method, path):
    attacker = _user(
        db, role=UserRole.USER, email="attacker@test.local", is_verified=False,
    )
    token = _token(attacker.email)
    r = api.request(method, path, headers={"Authorization": f"Bearer {token}"})
    # 400 is this codebase's existing convention for "authenticated but not verified"
    # (get_current_active_user, same as /auth/me) — it is blocked just the same as a 401/403
    # would be, just spelled differently. What matters is it is no longer 200/201/404.
    assert r.status_code in (400, 401, 403), f"{method} {path} -> {r.status_code}: {r.text}"


def test_unverified_self_registered_user_still_denied_me(db, api):
    """Sanity check that this account really is what the report describes: /auth/me itself
    (get_current_active_user, no role requirement) already rejected it before this fix —
    confirming the gap was specifically the business routers, not auth itself."""
    attacker = _user(
        db, role=UserRole.USER, email="attacker-me@test.local", is_verified=False,
    )
    token = _token(attacker.email)
    r = api.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 400, r.text


# --------------------------------------------------------------------------------------
# 4. Nothing that used to work for staff broke: MODERATOR and ROOT still clear the gate on
#    the routers that were upgraded (downstream 404s for a fake id are fine — that's them
#    passing authorization and hitting real business logic).
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/clients"),
        ("GET", "/funds/groups"),
        ("GET", "/operations"),
        ("GET", "/analyses"),
        ("GET", "/transactions"),
    ],
)
def test_moderator_still_works_on_upgraded_routes(db, api, method, path):
    moderator = _user(db, role=UserRole.MODERATOR, email="mod-still-works@test.local")
    token = _token(moderator.email)
    r = api.request(method, path, headers={"Authorization": f"Bearer {token}"})
    assert r.status_code not in (401, 403), f"{method} {path} -> {r.status_code}: {r.text}"


# --------------------------------------------------------------------------------------
# 5. H-4.4: scraping.py now requires MODERATOR+ (was fully unauthenticated).
# --------------------------------------------------------------------------------------


def test_scrape_manual_requires_auth(db, api):
    r = api.post("/scrape/manual")
    assert r.status_code == 401, r.text


def test_scrape_manual_rejects_user(db, api):
    user = _user(db, role=UserRole.USER, email="scrape-user@test.local")
    token = _token(user.email)
    r = api.post("/scrape/manual", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403, r.text


def test_scrape_status_and_latest_rates_require_auth(db, api):
    assert api.get("/scrape/status/some-task-id").status_code == 401
    assert api.get("/scrape/latest-rates").status_code == 401
