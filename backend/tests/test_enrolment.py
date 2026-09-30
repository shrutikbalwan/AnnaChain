"""Defect 3: anyone could re-key a device.

With no login: re-register node C with an attacker's key, declare a fake gap,
then have a record signed with the attacker's key accepted. The consignment
then told the buyer it had been altered, and the real node was locked out.
"""
from backend import auth as auth_mod, db

from .conftest import DEV, KEY
from .helpers import gap_mac, ingest, login, trip

OTHER = b"attacker-key-000000000000000000!"


def key_of(dev):
    return db.device(dev)["key_hex"]


def test_register_needs_a_login(client):
    r = client.post("/api/register", json={"device": DEV, "key_hex": KEY.hex()})
    assert r.status_code == 401
    assert db.device(DEV) is None


def test_register_new_device_with_a_login(client, auth):
    r = client.post("/api/register", headers=auth,
                    json={"device": DEV, "key_hex": KEY.hex()})
    assert r.status_code == 200
    assert key_of(DEV) == KEY.hex()
    rows = db.conn().execute("SELECT * FROM audit WHERE device_id=?", (DEV,)).fetchall()
    assert [r["action"] for r in rows] == ["enrol"]


def test_same_key_again_is_harmless(client, auth):
    """The capture replayer registers twice: once for the key, once for the label."""
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    r = client.post("/api/register", headers=auth,
                    json={"device": DEV, "key_hex": KEY.hex(), "label": "Crate A",
                          "truck": "T1"})
    assert r.status_code == 200
    assert db.device(DEV)["label"] == "Crate A"
    assert key_of(DEV) == KEY.hex()


def test_a_key_cannot_be_silently_replaced(client, auth):
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    r = client.post("/api/register", headers=auth,
                    json={"device": DEV, "key_hex": OTHER.hex()})
    assert r.status_code == 409
    assert key_of(DEV) == KEY.hex()


def test_rotation_needs_an_admin_and_is_audited(client, auth):
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    assert ingest(client, trip(KEY, DEV, 5)) == 5

    # an operator who is not an admin cannot do it
    salt, pw = auth_mod.hash_password("plain-operator")
    db.create_user("clerk", salt, pw, "operator")
    clerk = login(client, "clerk", "plain-operator")
    r = client.post("/api/register", headers=clerk,
                    json={"device": DEV, "key_hex": OTHER.hex(), "rotate": True})
    assert r.status_code == 403
    assert key_of(DEV) == KEY.hex()

    # an admin can, and it is written down
    r = client.post("/api/register", headers=auth,
                    json={"device": DEV, "key_hex": OTHER.hex(), "rotate": True})
    assert r.status_code == 200
    assert key_of(DEV) == OTHER.hex()
    rows = db.conn().execute(
        "SELECT * FROM audit WHERE device_id=? AND action='rotate_key'", (DEV,)).fetchall()
    assert len(rows) == 1 and rows[0]["who"] == "operator"

    # the records signed under the old key still verify under the old key
    assert client.get(f"/api/verify/{DEV}?full=true").json()["ok"] is True


def test_gap_with_a_wrong_mac_is_refused(client, auth):
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    assert ingest(client, trip(KEY, DEV, 3)) == 3
    for mac in (gap_mac(OTHER, DEV, 4, 5), None, "zz"):
        body = {"device": DEV, "from_seq": 4, "to_seq": 5}
        if mac is not None:
            body["mac"] = mac
        r = client.post("/api/gap", json=body)
        assert r.status_code == 401, (mac, r.text)
    assert db.gaps(DEV) == []
    assert db.device(DEV)["last_ack"] == 3


def test_gap_with_the_right_mac_is_accepted(client, auth):
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    assert ingest(client, trip(KEY, DEV, 3)) == 3
    r = client.post("/api/gap", json={"device": DEV, "from_seq": 4, "to_seq": 5,
                                      "mac": gap_mac(KEY, DEV, 4, 5)})
    assert r.status_code == 200
    assert len(db.gaps(DEV)) == 1


def test_a_replayed_gap_notice_is_refused(client, auth):
    """A signed notice for 4-5, sent again later, must not wind the server back."""
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    recs = trip(KEY, DEV, 10)
    assert ingest(client, recs[:3]) == 3
    notice = {"device": DEV, "from_seq": 4, "to_seq": 5, "mac": gap_mac(KEY, DEV, 4, 5)}
    assert client.post("/api/gap", json=notice).status_code == 200
    assert ingest(client, recs[5:]) == 5
    r = client.post("/api/gap", json=notice)
    assert r.status_code == 409
    assert db.device(DEV)["last_ack"] == 10
    assert len(db.gaps(DEV)) == 1


def test_the_attack_from_the_report_now_fails(client, auth):
    """Re-register, fake gap, forged record: every step refused, record intact."""
    client.post("/api/register", headers=auth, json={"device": DEV, "key_hex": KEY.hex()})
    assert ingest(client, trip(KEY, DEV, 20)) == 20
    assert client.post("/api/register",
                       json={"device": DEV, "key_hex": OTHER.hex()}).status_code == 401
    assert client.post("/api/gap", json={"device": DEV, "from_seq": 21, "to_seq": 21,
                                         "mac": gap_mac(OTHER, DEV, 21, 21)}).status_code == 401
    t = client.get(f"/api/trace/AC-{DEV:08X}").json()
    assert t["chain_ok"] is True and t["declared_lost"] == 0
