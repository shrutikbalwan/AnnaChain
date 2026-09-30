"""Build records the way the node does, so the server can be tested without one.

The layout mirrors lib/ac/ac_record.h: 52 bytes of body, then an HMAC-SHA256
over SHA-256(body). tools/selftest.cpp checks the C++ side of the same layout.
"""
import hashlib
import hmac
import struct
import time

STEP = 300                      # the node samples every 5 minutes


def body(dev, seq, ts, temp_c=4.0, rh=90.0, c2h4=0xFFFF, flags=0, batt=90,
         prev=b"\0" * 32):
    return struct.pack("<IIIhHHBB", dev, seq, ts, int(round(temp_c * 100)),
                       int(round(rh * 100)), c2h4, flags, batt) + prev


def sign(key: bytes, b: bytes) -> bytes:
    return hmac.new(key, hashlib.sha256(b).digest(), hashlib.sha256).digest()


def record(key, dev, seq, ts, prev=b"\0" * 32, **kw) -> bytes:
    b = body(dev, seq, ts, prev=prev, **kw)
    return b + sign(key, b)


def digest(raw: bytes) -> bytes:
    return hashlib.sha256(raw[:52]).digest()


def trip(key, dev, n, start=None, temps=None, **kw):
    """n chained records, 1..n, ending a few minutes ago. Returns the raw bytes.

    temps: optional function seq -> temperature."""
    start = start if start is not None else int(time.time()) - (n + 2) * STEP
    out, prev = [], b"\0" * 32
    for seq in range(1, n + 1):
        t = temps(seq) if temps else 4.0 + (seq % 7) * 0.05
        r = record(key, dev, seq, start + (seq - 1) * STEP, prev=prev, temp_c=t, **kw)
        out.append(r)
        prev = digest(r)
    return out


def gap_mac(key, dev, lo, hi) -> str:
    """What the node signs to declare records lo..hi lost (ac_record.cpp)."""
    d = hashlib.sha256(f"ACGAP|{dev}|{lo}|{hi}".encode()).digest()
    return hmac.new(key, d, hashlib.sha256).hexdigest()


def login(client, user="operator", password="annachain"):
    r = client.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def enrol(client, auth, dev, key, **kw):
    r = client.post("/api/register", headers=auth,
                    json={"device": dev, "key_hex": key.hex(), **kw})
    assert r.status_code == 200, r.text
    return r


def ingest(client, recs, batch=20):
    """Send in batches, the way the node drains a backlog."""
    total = 0
    for i in range(0, len(recs), batch):
        r = client.post("/api/ingest", json={"records": [x.hex() for x in recs[i:i + batch]]})
        assert r.status_code == 200, r.text
        total += r.json()["accepted"]
    return total
