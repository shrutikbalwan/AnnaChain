"""The eight checks in backend/checks.py, each with a record that passes and
one that fails. Nothing reaches the database unless all eight pass."""
import time

from backend import checks

from .helpers import record

KEY = b"annachain-test-key-for-checks-000"
DEV = 7
NOW = int(time.time())


def fresh():
    return checks.Verifier()


def ok(v, raw, key=KEY, seen=False, **kw):
    return v.check(raw, key, seen, now=NOW, **kw)


def test_1_device_must_be_registered():
    assert ok(fresh(), record(KEY, DEV, 1, NOW))[0]
    r = ok(fresh(), record(KEY, DEV, 1, NOW), key=None)
    assert r[0] is False and r[3] == "unknown device"


def test_2_signature_must_be_valid():
    raw = bytearray(record(KEY, DEV, 1, NOW))
    raw[12] ^= 1                                    # temperature changed after signing
    r = ok(fresh(), bytes(raw))
    assert r[0] is False and r[3] == "bad signature"
    r = ok(fresh(), record(b"some-other-key-0000000000000000", DEV, 1, NOW))
    assert r[0] is False and r[3] == "bad signature"


def test_3_sequence_must_be_next():
    v = fresh()
    r1 = record(KEY, DEV, 1, NOW)
    assert ok(v, r1)[0]
    r = ok(v, record(KEY, DEV, 3, NOW, prev=checks.digest(r1)))
    assert r[0] is False and "sequence gap" in r[3]
    assert ok(v, record(KEY, DEV, 2, NOW, prev=checks.digest(r1)))[0]


def test_4_duplicate_or_replay_is_refused():
    v = fresh()
    r1 = record(KEY, DEV, 1, NOW)
    assert ok(v, r1)[0]
    r = ok(v, r1)                                   # the same record again
    assert r[0] is False and r[3] == "duplicate or replay"
    r = ok(fresh(), r1, seen=True)                  # already in the database
    assert r[0] is False and r[3] == "duplicate or replay"


def test_5_timestamp_must_be_possible():
    assert ok(fresh(), record(KEY, DEV, 1, NOW - 300))[0]
    r = ok(fresh(), record(KEY, DEV, 1, 1000))
    assert r[0] is False and r[3] == "impossible timestamp"
    r = ok(fresh(), record(KEY, DEV, 1, NOW + 3600))
    assert r[0] is False and "ahead" in r[3]


def test_6_hash_chain_must_be_unbroken():
    v = fresh()
    r1 = record(KEY, DEV, 1, NOW)
    assert ok(v, r1)[0]
    r = ok(v, record(KEY, DEV, 2, NOW, prev=b"\x42" * 32))
    assert r[0] is False and r[3] == "broken hash chain"


def test_6_after_a_declared_gap_the_chain_re_anchors():
    v = fresh()
    r1 = record(KEY, DEV, 1, NOW)
    assert ok(v, r1)[0]
    v.note_gap(DEV, 3)
    assert ok(v, record(KEY, DEV, 4, NOW, prev=b"\x42" * 32))[0]


def test_7_lapsed_calibration_is_flagged_not_refused():
    v = fresh()
    r = ok(v, record(KEY, DEV, 1, NOW), cal_due=NOW - 90 * 86400)
    assert r[0] is True                             # the reading is still kept
    assert DEV in v.stale_calibration
    w = fresh()
    assert ok(w, record(KEY, DEV, 1, NOW), cal_due=NOW + 90 * 86400)[0]
    assert DEV not in w.stale_calibration


def test_8_impossible_reading_is_refused_but_a_declared_fault_is_kept():
    r = ok(fresh(), record(KEY, DEV, 1, NOW, temp_c=200.0))
    assert r[0] is False and "rated range" in r[3]
    r = ok(fresh(), record(KEY, DEV, 1, NOW, temp_c=200.0, flags=checks.FLAG_SENSORBAD))
    assert r[0] is True and r[1]["sensor_bad"]


def test_parse_matches_the_firmware_layout():
    p = checks.parse(record(KEY, DEV, 9, NOW, temp_c=-18.75, rh=91.23, c2h4=47,
                            flags=checks.FLAG_TAMPER, batt=63))
    assert (p["device"], p["seq"], p["ts"]) == (DEV, 9, NOW)
    assert p["temp_c"] == -18.75 and p["rh_pct"] == 91.23
    assert p["c2h4_ppb"] == 47 and p["tamper"] and p["battery_pct"] == 63
    assert checks.parse(record(KEY, DEV, 1, NOW))["c2h4_ppb"] is None   # not fitted
