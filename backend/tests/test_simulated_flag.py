"""A reading whose ethylene value was invented by the simulator says so.

`dump --ethylene` / `fleet --ethylene` make up an ethylene curve for a sensor
the board does not have. The record now carries FLAG_SIMULATED (bit 6) inside
the signature, and the server passes it on so both pages can badge it.
"""
from backend import checks

from .conftest import DEV, KEY
from .helpers import enrol, ingest, record, trip

SHIP = f"AC-{DEV:08X}"


def test_bit_6_parses_as_simulated():
    assert checks.FLAG_SIMULATED == 64
    assert checks.parse(record(KEY, DEV, 1, 1790294400, flags=64, c2h4=31))["simulated"]
    assert not checks.parse(record(KEY, DEV, 1, 1790294400))["simulated"]


def test_both_pages_are_told(client, auth):
    enrol(client, auth, DEV, KEY)
    assert ingest(client, trip(KEY, DEV, 10, flags=64, c2h4=30)) == 10
    s = client.get(f"/api/state?device={DEV}", headers=auth).json()
    assert all(p["sim"] for p in s["series"]) and s["simulated"] == 10
    t = client.get(f"/api/trace/{SHIP}").json()
    assert t["simulated_readings"] == 10 and all(p["sim"] for p in t["series"])


def test_an_honest_trip_is_not_badged(client, auth):
    enrol(client, auth, DEV, KEY)
    assert ingest(client, trip(KEY, DEV, 10)) == 10
    s = client.get(f"/api/state?device={DEV}", headers=auth).json()
    assert s["simulated"] == 0 and not any(p["sim"] for p in s["series"])
    assert client.get(f"/api/trace/{SHIP}").json()["simulated_readings"] == 0
