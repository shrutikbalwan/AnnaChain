"""Defect 5: the buyer's re-verify verified nothing.

The button asked the server, which resumed from its own mark and checked zero
records, and the page said "re-derived just now". The fix gives the buyer the
signed bytes and has the browser walk the chain itself. This file holds a
Python copy of what trace.html does, and checks it agrees with the server.
"""
import hashlib
import struct
from pathlib import Path

import pytest

from backend import db

from .conftest import DEV, KEY
from .helpers import enrol, gap_mac, ingest, trip

SHIP = f"AC-{DEV:08X}"
N = 120
TRACE_HTML = Path(__file__).resolve().parents[1] / "static" / "trace.html"


def all_records(client):
    out, offset = [], 0
    while True:
        r = client.get(f"/api/records/{SHIP}?offset={offset}&limit=50")
        assert r.status_code == 200, r.text
        j = r.json()
        out += j["records"]
        offset += len(j["records"])
        if offset >= j["total"] or not j["records"]:
            return j, out


def browser_walk(page, recs, series):
    """What trace.html does in the buyer's browser, line for line.

    Returns (ok, broken_at, why)."""
    gaps_to = {g["to"]: g["from"] for g in page["gaps"]}
    shown = {p["seq"]: p for p in series}
    prev_seq, prev_digest = 0, bytes(32)
    for rec in recs:
        raw = bytes.fromhex(rec["raw"])
        if len(raw) != 84:
            return False, rec["seq"], "wrong length"
        _, seq, ts, temp, _, _, flags, _ = struct.unpack_from("<IIIhHHBB", raw)
        if seq != rec["seq"]:
            return False, rec["seq"], "sequence number does not match"
        after_gap = (seq - 1) in gaps_to and gaps_to[seq - 1] == prev_seq + 1
        if seq != prev_seq + 1 and not after_gap:
            return False, seq, "a reading is missing"
        if not after_gap and raw[20:52] != prev_digest:
            return False, seq, "does not follow the reading before it"
        p = shown.get(seq)
        if p is not None:
            want = None if flags & 32 else temp / 100.0
            if (p["t"] is None) != (want is None) or \
               (want is not None and abs(p["t"] - want) > 0.005):
                return False, seq, "the page shows a different value from the signed one"
        prev_seq, prev_digest = seq, hashlib.sha256(raw[:52]).digest()
    return True, None, None


@pytest.fixture
def loaded(client, auth):
    enrol(client, auth, DEV, KEY)
    client.recs = trip(KEY, DEV, N)
    assert ingest(client, client.recs) == N
    return client


def test_records_need_no_token_and_are_84_bytes(loaded):
    page, recs = all_records(loaded)
    assert page["total"] == N and len(recs) == N
    assert all(len(bytes.fromhex(r["raw"])) == 84 for r in recs)


def test_records_are_exactly_what_was_ingested(loaded):
    _, recs = all_records(loaded)
    assert [bytes.fromhex(r["raw"]) for r in recs] == loaded.recs


def test_records_endpoint_is_paginated(loaded):
    j = loaded.get(f"/api/records/{SHIP}?offset=100&limit=10").json()
    assert [r["seq"] for r in j["records"]] == list(range(101, 111))
    assert loaded.get("/api/records/AC-NOPE").status_code == 404


def _both(client):
    page, recs = all_records(client)
    series = client.get(f"/api/trace/{SHIP}").json()["series"]
    mine = browser_walk(page, recs, series)
    server = client.get(f"/api/verify/{DEV}?full=true").json()
    return mine, server


def test_browser_walk_agrees_on_a_good_trip(loaded):
    (ok, at, _), server = _both(loaded)
    assert ok is True and server["ok"] is True


def test_browser_walk_agrees_on_the_doctored_display(loaded):
    """The demonstrated attack: raw untouched, only the displayed column changed."""
    c = db.conn()
    c.execute("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 100", (DEV,))
    c.commit()
    (ok, at, why), server = _both(loaded)
    assert ok is False and server["ok"] is False
    assert at == server["broken_at"] == 100


def test_browser_walk_agrees_on_a_flipped_byte(loaded):
    c = db.conn()
    raw = bytearray(c.execute("SELECT raw FROM records WHERE device_id=? AND seq=60",
                              (DEV,)).fetchone()["raw"])
    raw[16] ^= 0x01                         # the ethylene field; not shown on the page
    c.execute("UPDATE records SET raw=? WHERE device_id=? AND seq=60", (bytes(raw), DEV))
    c.commit()
    (ok, at, why), server = _both(loaded)
    assert ok is False and server["ok"] is False
    # The server checks the signature and names 60. The browser cannot check an
    # HMAC, so it sees the break one link later, where 61 no longer follows 60.
    assert server["broken_at"] == 60 and at == 61


def test_browser_walk_accepts_an_honest_gap(client, auth):
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 10)
    ingest(client, recs[:3])
    client.post("/api/gap", json={"device": DEV, "from_seq": 4, "to_seq": 5,
                                  "mac": gap_mac(KEY, DEV, 4, 5)})
    ingest(client, recs[5:])
    (ok, _, _), server = _both(client)
    assert ok is True and server["ok"] is True


def test_the_page_asks_for_a_full_walk_and_says_what_it_cannot_check():
    """The page itself: no cached answer, and no claim it cannot back."""
    html = TRACE_HTML.read_text(encoding="utf-8")
    assert "full=true" in html
    assert "/api/records/" in html
    assert "crypto.subtle.digest" in html
    assert "ATECC608B" in html                 # why the signature is not checked here
    assert "re-derived just now" not in html   # the claim that was false
