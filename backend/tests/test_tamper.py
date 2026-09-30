"""Defect 1: the server threw away the evidence.

The attack that was demonstrated: temp_c for record 200 edited directly in the
database, 3.9 -> 23.9 C. verify(full=True) said ok, and the buyer's page showed
the doctored peak under a green tick. Every test here is a way of changing the
stored record that must be caught.
"""
import pytest

from backend import db

from .conftest import DEV, KEY
from .helpers import enrol, ingest, trip

N = 250
SHIP = f"AC-{DEV:08X}"


@pytest.fixture
def loaded(client, auth):
    enrol(client, auth, DEV, KEY)
    assert ingest(client, trip(KEY, DEV, N)) == N
    return client


def full(client):
    r = client.get(f"/api/verify/{DEV}?full=true")
    assert r.status_code == 200, r.text
    return r.json()


def sql(q, *args):
    c = db.conn()
    c.execute(q, args)
    c.commit()


def test_good_trip_verifies(loaded):
    v = full(loaded)
    assert v["ok"] is True
    assert v["records"] == N and v["checked_now"] == N
    assert loaded.get(f"/api/trace/{SHIP}").json()["chain_ok"] is True


def test_the_raw_bytes_are_kept(loaded):
    row = db.conn().execute("SELECT raw FROM records WHERE device_id=? AND seq=1",
                            (DEV,)).fetchone()
    assert row["raw"] is not None and len(row["raw"]) == 84


def test_edited_temperature_column_is_caught(loaded):
    """The exact attack from the verification report."""
    sql("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 200", DEV)
    v = full(loaded)
    assert v["ok"] is False
    assert v["broken_at"] == 200
    assert v["reason"] == "column_mismatch"
    assert "temp_c" in v["columns"]


def test_flipped_byte_in_raw_is_a_bad_signature(loaded):
    raw = bytearray(db.conn().execute(
        "SELECT raw FROM records WHERE device_id=? AND seq=150", (DEV,)).fetchone()["raw"])
    raw[12] ^= 0x01                         # the temperature, inside the signed body
    sql("UPDATE records SET raw=? WHERE device_id=? AND seq=150", bytes(raw), DEV)
    v = full(loaded)
    assert v["ok"] is False
    assert v["broken_at"] == 150
    assert v["reason"] == "bad_signature"


def test_changed_stored_digest_is_caught(loaded):
    sql("UPDATE records SET digest=? WHERE device_id=? AND seq=100", "00" * 32, DEV)
    v = full(loaded)
    assert v["ok"] is False
    assert v["broken_at"] == 100


def test_deleted_record_is_caught(loaded):
    sql("DELETE FROM records WHERE device_id=? AND seq=120", DEV)
    v = full(loaded)
    assert v["ok"] is False
    assert v["broken_at"] == 121
    assert v["reason"] == "chain_break"


def test_every_parsed_column_is_compared(loaded):
    for col, val in (("ts", 1), ("rh_pct", 1.0), ("c2h4_ppb", 5), ("flags", 99),
                     ("batt_pct", 1)):
        before = db.conn().execute(
            f"SELECT {col} FROM records WHERE device_id=? AND seq=50", (DEV,)).fetchone()[0]
        sql(f"UPDATE records SET {col}=? WHERE device_id=? AND seq=50", val, DEV)
        v = full(loaded)
        assert v["ok"] is False and v["reason"] == "column_mismatch", col
        assert col in v["columns"], col
        sql(f"UPDATE records SET {col}=? WHERE device_id=? AND seq=50", before, DEV)
    assert full(loaded)["ok"] is True


@pytest.mark.parametrize("attack", ["column", "raw", "digest"])
def test_buyer_page_reports_all_three(loaded, attack):
    if attack == "column":
        sql("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 200", DEV)
    elif attack == "raw":
        raw = bytearray(db.conn().execute(
            "SELECT raw FROM records WHERE device_id=? AND seq=150", (DEV,)).fetchone()["raw"])
        raw[12] ^= 0x01
        sql("UPDATE records SET raw=? WHERE device_id=? AND seq=150", bytes(raw), DEV)
    else:
        sql("UPDATE records SET digest=? WHERE device_id=? AND seq=100", "00" * 32, DEV)
    t = loaded.get(f"/api/trace/{SHIP}").json()
    assert t["chain_ok"] is False
    assert t["tone"] == "bad"


def test_buyer_page_does_not_trust_a_stored_mark(loaded):
    """A resumable mark is an operator shortcut. The buyer's verdict must walk
    everything, or an edit before the mark goes unseen."""
    assert loaded.get(f"/api/verify/{DEV}").json()["ok"] is True     # lays a mark at 250
    sql("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 200", DEV)
    assert loaded.get(f"/api/trace/{SHIP}").json()["chain_ok"] is False


def test_a_record_without_evidence_is_not_verified(loaded):
    """A database from before the raw column existed: rows with no evidence.
    A fresh schema forbids that (raw is NOT NULL), so rebuild the table the
    way a migrated one looks."""
    c = db.conn()
    c.executescript("CREATE TABLE r2 AS SELECT * FROM records; DROP TABLE records; "
                    "ALTER TABLE r2 RENAME TO records;")
    c.commit()
    sql("UPDATE records SET raw=NULL WHERE device_id=? AND seq=10", DEV)
    v = full(loaded)
    assert v["ok"] is False
    assert v["broken_at"] == 10
    assert v["reason"] == "missing_raw"
