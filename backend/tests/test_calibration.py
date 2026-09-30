"""Check 7 through the API: a calibration can be recorded without touching SQLite,
and a reading from a sensor whose EN 13486 verification has lapsed is kept,
alerted on, and stored marked as uncertified.

Before this, db.set_calibration() had no endpoint, so check 7 could only ever
be exercised by editing the database by hand.
"""
import time

from backend import db

from .conftest import DEV, KEY
from .helpers import enrol, ingest, trip

DAY = 86400


def calibrate(client, auth, days_ago, months=12, dev=DEV):
    return client.post(f"/api/calibration/{dev}", headers=auth,
                       json={"cal_date": time.time() - days_ago * DAY, "months": months,
                             "ref": "test bench, reference thermometer #1"})


def stored_marks(dev=DEV):
    return [r["uncertified"] for r in db.conn().execute(
        "SELECT uncertified FROM records WHERE device_id=? ORDER BY seq", (dev,))]


def kinds(dev=DEV):
    return [a["kind"] for a in db.alerts(100, device_id=dev)]


def test_setting_calibration_needs_a_login(client):
    r = client.post(f"/api/calibration/{DEV}", json={"cal_date": time.time(), "months": 12})
    assert r.status_code == 401


def test_an_unknown_device_is_404(client, auth):
    assert calibrate(client, auth, 10, dev=12345).status_code == 404


def test_a_lapsed_calibration_alerts_and_marks_the_reading(client, auth):
    enrol(client, auth, DEV, KEY)
    r = calibrate(client, auth, days_ago=400)            # 12 months, 400 days ago
    assert r.status_code == 200, r.text
    assert r.json()["due"] < time.time()
    assert ingest(client, trip(KEY, DEV, 3)) == 3        # kept, not refused
    assert "calibration" in kinds()
    assert stored_marks() == [1, 1, 1]
    s = client.get(f"/api/state?device={DEV}", headers=auth).json()
    assert all(p["u"] for p in s["series"])


def test_an_in_date_calibration_marks_nothing(client, auth):
    enrol(client, auth, DEV, KEY)
    assert calibrate(client, auth, days_ago=30).status_code == 200
    assert ingest(client, trip(KEY, DEV, 3)) == 3
    assert "calibration" not in kinds()
    assert stored_marks() == [0, 0, 0]


def test_it_is_audited_and_validated(client, auth):
    enrol(client, auth, DEV, KEY)
    assert calibrate(client, auth, 10, months=0).status_code == 400
    future = client.post(f"/api/calibration/{DEV}", headers=auth,
                         json={"cal_date": time.time() + 30 * DAY, "months": 12})
    assert future.status_code == 400
    assert calibrate(client, auth, 10).status_code == 200
    rows = db.conn().execute("SELECT * FROM audit WHERE action='calibration'").fetchall()
    assert len(rows) == 1 and rows[0]["device_id"] == DEV
