"""A node the truck voted against stays suspect across a restart, and a node
that was cleared stays cleared.

AlertEngine.suspect lived only in memory: restart the server and a drifting
sensor's temperature alarms went live again, as if nothing had been decided.
The fix persists it the way login_throttle is persisted (a table in db.py,
loaded at startup), written when a node is flagged and deleted when it is
cleared — otherwise a recovered node would come back suspect after the next
restart, the same bug pointing the other way.
"""
from fastapi.testclient import TestClient

from backend import app as app_mod, db
from backend.alerts import DISAGREE_RUNS, EXCURSION_MIN, AlertEngine

SHIP = {"min_c": 2.0, "max_c": 8.0}
ODD = 3


def flag(engine):
    for k in range(DISAGREE_RUNS):
        engine.diagnose("T1", 1790400000 + k * 300, {1: 4.0, 2: 4.1, ODD: 9.0})
    db.commit()                    # as app._diagnose does after each diagnosis
    assert engine.suspect == {ODD}


def rebuilt():
    """A new engine over the same database, as after a restart."""
    e = AlertEngine(db)
    e.load()
    return e


def hot(engine, dev=ODD, n=EXCURSION_MIN + 1):
    for seq in range(1, n + 1):
        engine.on_record({"device": dev, "seq": seq, "ts": 1790400000 + seq * 300,
                          "temp_c": 11.0, "c2h4_ppb": None, "tamper": False,
                          "sensor_bad": False, "battery_pct": 90}, SHIP)
    db.commit()


def temp_alerts(dev=ODD):
    return [a for a in db.alerts(100, device_id=dev) if a["kind"] == "temp_high"]


def rows():
    return db.conn().execute("SELECT * FROM suspects").fetchall()


def test_a_suspect_node_is_still_suspect_after_a_restart(client):
    flag(app_mod.engine)
    e = rebuilt()
    assert e.suspect == {ODD}
    hot(e)
    assert temp_alerts() == []                  # its alarms are still held
    r = rows()
    assert len(r) == 1 and r[0]["device_id"] == ODD and r[0]["truck"] == "T1"
    assert r[0]["flagged"] > 0 and '"3": 9.0' in r[0]["readings"]


def test_the_server_reloads_it_on_startup(client):
    flag(app_mod.engine)
    app_mod.engine.__init__(db)                 # the process restarts...
    with TestClient(app_mod.app):               # ...and its startup runs
        assert app_mod.engine.suspect == {ODD}


def test_a_cleared_node_stays_cleared_after_a_restart(client):
    flag(app_mod.engine)
    rebuilt().clear_suspect(ODD)
    db.commit()
    assert rows() == []
    e = rebuilt()
    assert e.suspect == set()
    hot(e)
    assert len(temp_alerts()) == 1              # trusted again: its alarms are live


def test_reset_leaves_no_suspect(client, auth):
    flag(app_mod.engine)
    assert client.post("/api/reset", headers=auth).status_code == 200
    assert rows() == []
    assert app_mod.engine.suspect == set()
    assert rebuilt().suspect == set()


def test_agreeing_buckets_clear_it_in_the_database(client):
    """P3: nothing called clear_suspect(), so a recovered node stayed suspect
    for ever, and since S4 that was durable."""
    flag(app_mod.engine)
    for k in range(DISAGREE_RUNS):
        app_mod.engine.diagnose("T1", 1790400000 + (DISAGREE_RUNS + k) * 300,
                                {1: 4.0, 2: 4.1, ODD: 4.2})
    db.commit()
    assert app_mod.engine.suspect == set()
    assert rows() == []
    assert rebuilt().suspect == set()
    assert [a["kind"] for a in db.alerts(100, device_id=ODD)][0] == "sensor_agrees"
