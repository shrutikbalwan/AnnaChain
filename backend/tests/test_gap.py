"""Defect 2: an honest gap was reported to the buyer as fraud.

A node sent 1-3, declared 4-5 lost to a flash overrun, then sent 6-10. The
buyer's page said "The record has been altered". The node did exactly what the
design promises, and was called a liar for it.
"""
import pytest

from backend import db

from .conftest import DEV, KEY
from .helpers import digest, enrol, gap_mac, ingest, record, trip

SHIP = f"AC-{DEV:08X}"


@pytest.fixture
def gapped(client, auth):
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 10)                   # the node's whole chain, 1..10
    assert ingest(client, recs[0:3]) == 3
    r = client.post("/api/gap", json={"device": DEV, "from_seq": 4, "to_seq": 5,
                                      "mac": gap_mac(KEY, DEV, 4, 5)})
    assert r.status_code == 200, r.text
    assert ingest(client, recs[5:10]) == 5      # 4 and 5 never arrive
    client.recs = recs
    return client


def test_honest_gap_verifies_clean(gapped):
    v = gapped.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is True, v
    assert v["records"] == 8
    assert v["declared_gaps"] == [{"from": 4, "to": 5}]


def test_buyer_sees_a_declared_hole_not_fraud(gapped):
    t = gapped.get(f"/api/trace/{SHIP}").json()
    assert t["chain_ok"] is True
    assert t["verdict"] == "Complete, with a declared hole"
    assert t["declared_lost"] == 2
    assert "altered" not in t["verdict"].lower()


def test_a_real_edit_after_the_gap_is_still_caught(gapped):
    c = db.conn()
    c.execute("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 8", (DEV,))
    c.commit()
    v = gapped.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is False and v["broken_at"] == 8
    assert gapped.get(f"/api/trace/{SHIP}").json()["verdict"] == "The record has been altered"


def test_a_signed_but_misplaced_record_after_the_gap_is_a_chain_break(gapped):
    """Record 7 re-signed by someone holding the key, but not chained to 6."""
    old = gapped.recs[6]
    forged = record(KEY, DEV, 7, int.from_bytes(old[8:12], "little"),
                    prev=b"\x11" * 32, temp_c=4.0)
    c = db.conn()
    c.execute("UPDATE records SET raw=?, prev=?, digest=?, sig=? WHERE device_id=? AND seq=7",
              (forged, forged[20:52].hex(), digest(forged).hex(), forged[52:84].hex(), DEV))
    c.commit()
    v = gapped.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is False and v["broken_at"] == 7 and v["reason"] == "chain_break"


def test_a_gap_row_inserted_in_the_database_is_not_honoured(client, auth):
    """A declared gap is evidence too. One written straight into the table,
    without the device's signature, must not excuse a missing record."""
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 10)
    assert ingest(client, recs) == 10
    c = db.conn()
    c.execute("DELETE FROM records WHERE device_id=? AND seq IN (4,5)", (DEV,))
    c.execute("INSERT INTO gaps(device_id,from_seq,to_seq,declared) VALUES(?,?,?,0)",
              (DEV, 4, 5))
    c.commit()
    v = client.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is False
