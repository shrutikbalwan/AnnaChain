"""The whole authentication boundary: commercial data needs a login, the
buyer's surface never does."""
import pytest

from backend import ledger

from .conftest import DEV, KEY
from .helpers import enrol, ingest, login, trip

SHIP = f"AC-{DEV:08X}"

PROTECTED = [
    ("get", "/api/state"), ("get", "/api/shipments"), ("get", "/api/ledger"),
    ("get", "/api/me"), ("get", "/api/truck/T1"),
    ("post", f"/api/anchor/{DEV}"), ("post", "/api/reset"),
    ("post", "/api/register"), ("post", "/api/checkpoint"),
]

PUBLIC = [
    "/", f"/t/{SHIP}", f"/label/{SHIP}", f"/api/trace/{SHIP}", f"/api/verify/{DEV}",
    f"/api/epcis/{SHIP}", f"/api/shelflife/{SHIP}", f"/api/records/{SHIP}",
    f"/api/qr/{SHIP}",
]


@pytest.fixture
def loaded(client, auth):
    enrol(client, auth, DEV, KEY)
    ingest(client, trip(KEY, DEV, 20))
    return client


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_without_a_token(loaded, method, path):
    r = getattr(loaded, method)(path, **({"json": {}} if method == "post" else {}))
    assert r.status_code == 401, (path, r.status_code)


@pytest.mark.parametrize("method,path", PROTECTED)
def test_protected_with_a_bad_token(loaded, method, path):
    h = {"Authorization": "Bearer not-a-real-token"}
    r = getattr(loaded, method)(path, headers=h,
                                **({"json": {}} if method == "post" else {}))
    assert r.status_code == 401, (path, r.status_code)


@pytest.mark.parametrize("path", PUBLIC)
def test_public_without_a_token(loaded, path):
    r = loaded.get(path)
    assert r.status_code == 200, (path, r.status_code)


def test_unknown_trace_is_404_not_401(loaded):
    assert loaded.get("/api/trace/nope").status_code == 404


def test_wrong_password_and_unknown_user_look_the_same(client):
    a = client.post("/api/login", json={"username": "operator", "password": "wrong"})
    b = client.post("/api/login", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_a_token_works_and_logout_ends_it(client):
    h = login(client)
    assert client.get("/api/me", headers=h).status_code == 200
    client.post("/api/logout", headers=h)
    assert client.get("/api/me", headers=h).status_code == 401


def test_ledger_says_it_is_not_distributed_even_when_empty(client, auth, tmp_path):
    """F1: the empty ledger used to leave `distributed` out altogether."""
    assert ledger.LocalLedger(tmp_path / "empty.jsonl").verify()["distributed"] is False
    j = client.get("/api/ledger", headers=auth).json()
    assert j["entries"] == 0 and j["distributed"] is False


def test_favicon_is_not_a_404(client):
    """F4: every page logged a 404 for it."""
    assert client.get("/favicon.ico").status_code in (200, 204)
