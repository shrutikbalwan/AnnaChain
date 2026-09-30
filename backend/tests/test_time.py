"""Defect 4: the timestamp check could not catch a wrong clock.

It only asked for a date between 2020 and 2039. A reading ten years ahead, a
year behind, or earlier than the reading before it all passed.
"""
import time

from backend import checks

from .conftest import DEV, KEY
from .helpers import enrol, ingest, record, trip

NOW = 1790400000          # a fixed "server now" for the unit-level tests


def check(v, raw, now=NOW):
    return v.check(raw, KEY, False, now=now)


def test_a_reading_on_time_is_accepted():
    ok, *_ , reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW - 600))
    assert ok, reason


def test_thirty_seconds_of_skew_is_accepted():
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW + 30))
    assert ok, reason


def test_far_future_is_refused():
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW + 10 * 365 * 86400))
    assert not ok and "ahead" in reason


def test_just_past_the_skew_allowance_is_refused():
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW + checks.MAX_SKEW_S + 1))
    assert not ok and "ahead" in reason


def test_time_running_backwards_is_refused():
    v = checks.Verifier()
    r1 = record(KEY, DEV, 1, NOW - 600)
    assert check(v, r1)[0]
    r2 = record(KEY, DEV, 2, NOW - 900, prev=checks.digest(r1))
    ok, *_, reason = check(v, r2)
    assert not ok and "backwards" in reason


def test_a_year_old_clock_is_refused():
    """What src/main.cpp would have produced: every reading a year old."""
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW - 365 * 86400))
    assert not ok and "older" in reason


def test_a_backlog_the_flash_could_hold_is_accepted():
    """14 days is what a 4096-record flash holds at 5-minute sampling."""
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, NOW - 14 * 86400))
    assert ok, reason


def test_the_rules_hold_through_the_api(client, auth):
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 3)
    assert ingest(client, recs) == 3
    back = record(KEY, DEV, 4, int(time.time()) - 86400, prev=checks.digest(recs[-1]))
    r = client.post("/api/ingest", json={"records": [back.hex()]}).json()
    assert r["accepted"] == 0 and "backwards" in r["reason"]


def test_time_survives_a_restart(client, auth):
    """The last accepted time must be reloaded, or a restart re-opens the hole."""
    from backend import app as app_mod, db
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 3)
    ingest(client, recs)
    app_mod.verifier.__init__()
    for d in db.devices():
        app_mod.verifier.load(d["device_id"], d["last_ack"], d["tip_digest"],
                              d["anchor_next"], d["last_ts"])
    back = record(KEY, DEV, 4, int(time.time()) - 86400, prev=checks.digest(recs[-1]))
    ok, *_, reason = app_mod.verifier.check(back, KEY, False)
    assert not ok and "backwards" in reason
