"""Login rate-limiting: repeated wrong passwords back off, per username and per
source, and the delay is stated in the response rather than left to guesswork.
The clock is a fake one, so "after the window" does not mean a slow test."""
import pytest

from backend import app as app_mod, auth as auth_mod

from .helpers import login

GOOD = {"username": "operator", "password": "annachain"}
BAD = {"username": "operator", "password": "wrong"}


class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock(client, monkeypatch):
    c = FakeClock()
    monkeypatch.setattr(app_mod.throttle, "clock", c)
    return c


def test_twenty_wrong_passwords_lock_the_account(client, clock):
    codes = [client.post("/api/login", json=BAD).status_code for _ in range(20)]
    assert codes[:auth_mod.FREE_ATTEMPTS - 1] == [401] * (auth_mod.FREE_ATTEMPTS - 1)
    assert 429 in codes
    r = client.post("/api/login", json=BAD)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0
    assert r.json()["retry_after_s"] == int(r.headers["Retry-After"])
    assert "try again" in r.json()["detail"]


def test_the_right_password_is_refused_during_the_lockout(client, clock):
    for _ in range(20):
        client.post("/api/login", json=BAD)
    r = client.post("/api/login", json=GOOD)
    assert r.status_code == 429          # otherwise the lockout is just a speed bump


def test_the_right_password_works_after_the_window(client, clock):
    for _ in range(20):
        client.post("/api/login", json=BAD)
    wait = client.post("/api/login", json=GOOD).json()["retry_after_s"]
    assert 0 < wait <= auth_mod.MAX_DELAY_S
    clock.t += wait + 1
    r = client.post("/api/login", json=GOOD)
    assert r.status_code == 200 and r.json()["token"]
    # and success clears the slate
    assert client.post("/api/login", json=BAD).status_code == 401


def test_the_delay_grows_with_repeated_failures(client, clock):
    delays = []
    for _ in range(auth_mod.FREE_ATTEMPTS + 3):
        r = client.post("/api/login", json=BAD)
        delays.append(r.json().get("retry_after_s", 0))
        clock.t += delays[-1] + 1            # wait out each lock, then fail again
    assert delays[:auth_mod.FREE_ATTEMPTS - 1] == [0] * (auth_mod.FREE_ATTEMPTS - 1)
    locked = [d for d in delays if d]
    assert locked == sorted(locked) and locked[-1] > locked[0]


def test_the_failure_that_trips_the_lock_says_so(client, clock):
    for _ in range(auth_mod.FREE_ATTEMPTS - 1):
        assert client.post("/api/login", json=BAD).json()["retry_after_s"] == 0
    r = client.post("/api/login", json=BAD)
    assert r.status_code == 429 and r.json()["retry_after_s"] == auth_mod.BASE_DELAY_S


def test_spraying_usernames_from_one_source_is_throttled(client, clock):
    """Per source: a different username on every guess does not get around it."""
    codes = [client.post("/api/login", json={"username": f"user{i}", "password": "x"}
                         ).status_code for i in range(10)]
    assert 429 in codes


def test_one_username_is_throttled_across_sources(clock):
    """Per username: guesses at one account from many addresses still add up."""
    t = auth_mod.Throttle(clock=clock)
    for i in range(auth_mod.FREE_ATTEMPTS):
        t.failure("operator", f"10.0.0.{i}")
    assert t.retry_after("operator", "10.9.9.9") > 0
    assert t.retry_after("someone-else", "10.9.9.9") == 0


def test_username_case_does_not_dodge_the_count(clock):
    t = auth_mod.Throttle(clock=clock)
    for i, name in enumerate(["Operator", "OPERATOR", "operator", "oPeRaToR", "operator"]):
        t.failure(name, f"10.0.0.{i}")
    assert t.retry_after("operator", "10.1.1.1") > 0


def test_a_normal_login_is_untouched(client, clock):
    assert client.post("/api/login", json=BAD).status_code == 401
    assert login(client)                       # one typo does not lock anyone out
