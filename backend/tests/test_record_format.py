"""The record format's version rule (docs/CRYPTO.md).

v1 is the 84-byte record every node, capture and database holds today. It has
no version byte and never will: its bytes are the evidence, and adding a byte
would change every one of them. It is identified by its length. Every later
format starts with a version byte (2 or more) and has a fixed length of its own
that is never 84. These tests pin both halves: existing v1 bytes still verify
unchanged, and a record in a format this server does not know is refused by
name, not mistaken for a v1 record or waved through.
"""
from backend import checks

from .conftest import DEV, KEY
from .helpers import enrol, ingest, record, trip

# A v1 record frozen as bytes. tools/selftest.cpp builds the same record with
# the C++ encoder and signer and checks it against the same hex, so the two
# sides cannot drift apart without one of them going red.
GOLDEN_V1 = bytes.fromhex(
    "012023260700000080b9b56aadf8a3232f00093f00070e151c232a31383f464d545b"
    "626970777e858c939aa1a8afb6bdc4cbd2d99b3d31af2a558f4021ac074f5f111202"
    "114de7378ea36521c4f47f3e7dc97f32")


def test_the_golden_v1_record_is_v1_and_verifies_unchanged():
    assert len(GOLDEN_V1) == 84
    assert checks.record_format(GOLDEN_V1) == checks.FORMAT_V1 == 1
    p = checks.parse(GOLDEN_V1)
    assert (p["device"], p["seq"], p["ts"]) == (DEV, 7, 1790294400)
    assert p["temp_c"] == -18.75 and p["c2h4_ppb"] == 47 and p["flags"] == 9
    # the signature still verifies over the same 52 bytes, with no version byte
    v = checks.Verifier()
    v.load(DEV, 6, None, anchor_next=True)     # the server already holds 1-6
    ok, *_, reason = v.check(GOLDEN_V1, KEY, False, now=1790294400 + 60)
    assert ok, reason


def test_a_v1_record_whose_first_byte_is_2_is_still_v1():
    """Byte 0 of a v1 record is the low byte of the device id. Node B is
    0x26232002, so its records start with 0x02 — which is exactly why v1 is told
    apart by length and never by its first byte."""
    r = record(KEY, 0x26232002, 1, 1790294400)
    assert r[0] == 2 and checks.record_format(r) == checks.FORMAT_V1


def test_a_stored_v1_chain_still_verifies(client, auth):
    enrol(client, auth, DEV, KEY)
    assert ingest(client, trip(KEY, DEV, 30)) == 30
    v = client.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is True and v["records"] == 30


def test_a_later_format_is_refused_by_name(client, auth):
    """A 117-byte record starting with version 2 (the planned ECDSA format).
    This server cannot check it, so it is refused, and says why."""
    enrol(client, auth, DEV, KEY)
    v2 = bytes([2]) + record(KEY, DEV, 1, 1790294400)[:52] + bytes(64)
    assert checks.record_format(v2) is None
    r = client.post("/api/ingest", json={"records": [v2.hex()]}).json()
    assert r["accepted"] == 0
    assert "record format version 2" in r["reason"], r["reason"]


def test_version_1_never_carries_a_version_byte(client, auth):
    """An 85-byte "v1 with a version byte" is not a format: v1 is 84 bytes."""
    enrol(client, auth, DEV, KEY)
    bad = bytes([1]) + record(KEY, DEV, 1, 1790294400)
    assert checks.record_format(bad) is None
    r = client.post("/api/ingest", json={"records": [bad.hex()]}).json()
    assert r["accepted"] == 0 and "record format version 1" in r["reason"], r["reason"]
