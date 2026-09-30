"""P1: the node has no clock until a server gives it one.

A record taken before first contact carries FLAG_TIMEUNSET (bit 7): its
timestamp is uptime counted from the build time, not wall-clock time. The
server accepts it with that mark instead of refusing it as older than a node
can hold a reading (check 5, MAX_HOLD_S), because it makes no wall-clock claim.
The mark cannot become a way round check 5: once a device has had a record
accepted WITHOUT the flag, its clock has been set, and a later flagged record
is refused as inconsistent. That state is persisted (devices.clock_set).

Monotonic time across the switch: flagged timestamps are compared with each
other (a reboot must not go backwards) and wall-clock timestamps with each
other. The first wall-clock record is not compared with the flagged ones
before it: the two are different clocks, and the node takes the server's time
as it finds it (docs/HIL.md step 1).
"""
import time

from backend import app as app_mod, bridge_serial, checks, db

from .conftest import DEV, KEY
from .helpers import digest, enrol, record

NOW = 1790400000
TU = checks.FLAG_TIMEUNSET
OLD = NOW - checks.MAX_HOLD_S - 5 * 86400          # older than a node can hold a reading


def check(v, raw, now=NOW):
    return v.check(raw, KEY, False, now=now)


def test_the_flag_is_bit_7():
    assert TU == 0x80
    assert checks.parse(record(KEY, DEV, 1, NOW, flags=TU))["time_unset"]
    assert not checks.parse(record(KEY, DEV, 1, NOW))["time_unset"]


def test_a_flagged_record_older_than_max_hold_is_accepted_and_marked():
    ok, parsed, _, reason = check(checks.Verifier(), record(KEY, DEV, 1, OLD, flags=TU))
    assert ok, reason
    assert parsed["time_unset"] is True


def test_the_same_record_without_the_flag_is_still_refused():
    ok, *_, reason = check(checks.Verifier(), record(KEY, DEV, 1, OLD))
    assert not ok and "older" in reason


def test_a_flagged_record_after_the_clock_was_set_is_refused_as_inconsistent():
    v = checks.Verifier()
    r1 = record(KEY, DEV, 1, NOW - 600, flags=TU)
    r2 = record(KEY, DEV, 2, NOW - 300, prev=digest(r1))            # clock now set
    r3 = record(KEY, DEV, 3, NOW - 200, prev=digest(r2), flags=TU)
    assert check(v, r1)[0] and check(v, r2)[0]
    ok, *_, reason = check(v, r3)
    assert not ok and "inconsistent" in reason


def test_a_flagged_record_ahead_of_the_server_is_still_refused():
    """The build-time base is never ahead of true time, so ahead is wrong either way."""
    ok, *_, reason = check(checks.Verifier(),
                           record(KEY, DEV, 1, NOW + checks.MAX_SKEW_S + 60, flags=TU))
    assert not ok and "ahead" in reason


def test_flagged_time_may_not_run_backwards_either():
    v = checks.Verifier()
    r1 = record(KEY, DEV, 1, OLD, flags=TU)
    r2 = record(KEY, DEV, 2, OLD - 60, prev=digest(r1), flags=TU)
    assert check(v, r1)[0]
    ok, *_, reason = check(v, r2)
    assert not ok and "backwards" in reason


def test_the_switch_to_wall_clock_is_not_compared_with_uptime_time():
    """The first wall-clock record may be earlier than a flagged one before it
    (a build machine a little ahead); after the switch, wall-clock time is
    monotonic as always."""
    v = checks.Verifier()
    r1 = record(KEY, DEV, 1, NOW - 100, flags=TU)
    r2 = record(KEY, DEV, 2, NOW - 400, prev=digest(r1))
    r3 = record(KEY, DEV, 3, NOW - 500, prev=digest(r2))
    assert check(v, r1)[0]
    ok, *_, reason = check(v, r2)
    assert ok, reason
    ok, *_, reason = check(v, r3)
    assert not ok and "backwards" in reason


def _restart():
    """What the server's startup does (app.lifespan)."""
    app_mod.verifier.__init__()
    for d in db.devices():
        app_mod.verifier.load(d["device_id"], d["last_ack"], d["tip_digest"],
                              d["anchor_next"], d["last_ts"], d["clock_set"])


def test_clock_set_survives_a_restart(client, auth):
    enrol(client, auth, DEV, KEY)
    now = int(time.time())
    r1 = record(KEY, DEV, 1, now - 900, flags=TU)
    r2 = record(KEY, DEV, 2, now - 600, prev=digest(r1))
    res = client.post("/api/ingest", json={"records": [r1.hex(), r2.hex()]}).json()
    assert res["accepted"] == 2, res
    _restart()
    r3 = record(KEY, DEV, 3, now - 300, prev=digest(r2), flags=TU)
    res = client.post("/api/ingest", json={"records": [r3.hex()]}).json()
    assert res["accepted"] == 0 and "inconsistent" in res["reason"]


def test_the_dashboard_and_trace_page_say_so(client, auth):
    enrol(client, auth, DEV, KEY)
    now = int(time.time())
    r1 = record(KEY, DEV, 1, now - 900, flags=TU)
    r2 = record(KEY, DEV, 2, now - 600, prev=digest(r1))
    assert client.post("/api/ingest", json={"records": [r1.hex(), r2.hex()]}).json()["accepted"] == 2
    s = client.get(f"/api/state?device={DEV}", headers=auth).json()
    assert [p["tu"] for p in s["series"]] == [True, False]
    assert s["time_unset"] == 1
    t = client.get(f"/api/trace/AC-{DEV:08X}").json()
    assert t["time_unset_readings"] == 1
    assert [p["tu"] for p in t["series"]] == [True, False]
    for page in ("/", f"/t/AC-{DEV:08X}"):
        assert "relative to power-up, not wall clock" in client.get(page).text


def test_lastack_carries_the_servers_time(client, auth):
    enrol(client, auth, DEV, KEY)
    before = time.time()
    r = client.get(f"/api/lastack/{DEV}").json()
    assert r["last_ack"] == 0
    assert before - 1 <= r["now"] <= time.time() + 1 and isinstance(r["now"], int)


def test_the_serial_bridge_passes_the_time_down(client, monkeypatch):
    """Q <dev> is answered "A <seq> <unix>". A board flashed before the time
    field existed reads the seq with toInt(), which stops at the space."""
    import io
    import urllib.error

    import pytest
    serial = pytest.importorskip("serial")

    def call(base, path, payload=None, method="POST", token=None):
        h = {"Authorization": "Bearer " + token} if token else {}
        r = (client.post(path, json=payload, headers=h) if method == "POST"
             else client.get(path, headers=h))
        if r.status_code >= 400:
            raise urllib.error.HTTPError(path, r.status_code, r.text, {}, io.BytesIO(r.content))
        return r.json()

    class Port:
        def __init__(self, lines):
            self.lines = [l.encode() + b"\n" for l in lines]
            self.written = []

        def readline(self):
            if not self.lines:
                raise serial.SerialException("closed")
            return self.lines.pop(0)

        def write(self, b):
            self.written.append(b.decode())

    port = Port([f"K {DEV} {KEY.hex()}", f"Q {DEV}", f"Q {DEV + 1}"])
    monkeypatch.setattr(bridge_serial, "call", call)
    monkeypatch.setattr(serial, "Serial", lambda *a, **k: port)
    with pytest.raises(SystemExit):
        bridge_serial.main(["COM9"])
    known, unknown = port.written
    word, seq, t = known.split()
    assert word == "A" and seq == "0" and abs(int(t) - time.time()) < 5
    assert unknown == "A 0\n"          # not enrolled: no server answer, so no time
