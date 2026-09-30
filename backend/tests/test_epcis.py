"""GS1 EPCIS events. A real past bug: adding a checkpoint made the
commissioning event disappear."""
from .conftest import DEV, KEY
from .helpers import enrol, ingest, trip

SHIP = f"AC-{DEV:08X}"


def steps(client):
    j = client.get(f"/api/epcis/{SHIP}").json()
    return [(e["action"], e["bizStep"].split(":")[-1]) for e in j["epcisBody"]["eventList"]]


def test_commissioning_survives_a_checkpoint(client, auth):
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 40)
    ingest(client, recs)
    assert steps(client) == [("ADD", "commissioning"), ("OBSERVE", "receiving")]

    mid = int.from_bytes(recs[20][8:12], "little")
    r = client.post("/api/checkpoint", headers=auth,
                    json={"shipment_id": SHIP, "place": "Dhule checkpoint",
                          "biz_step": "inspecting", "ts": mid})
    assert r.status_code == 200
    assert steps(client) == [("ADD", "commissioning"), ("OBSERVE", "inspecting"),
                             ("OBSERVE", "receiving")]


def test_each_leg_carries_its_own_readings(client, auth):
    enrol(client, auth, DEV, KEY)
    ingest(client, trip(KEY, DEV, 20))
    events = client.get(f"/api/epcis/{SHIP}").json()["epcisBody"]["eventList"]
    rep = events[-1]["sensorElementList"][0]["sensorReport"][0]
    assert rep["type"] == "gs1:Temperature" and rep["uom"] == "CEL"
    assert rep["minValue"] <= rep["meanValue"] <= rep["maxValue"]


def test_a_checkpoint_needs_a_login(client, auth):
    enrol(client, auth, DEV, KEY)
    ingest(client, trip(KEY, DEV, 5))
    r = client.post("/api/checkpoint", json={"shipment_id": SHIP, "place": "x"})
    assert r.status_code == 401
