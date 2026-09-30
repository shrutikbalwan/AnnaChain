"""P4: the demo and the real board shared device id 26232001.

On a database that had seen a demo, a real board was refused and every record
failed "bad signature": the demo had enrolled a different key under that id.
Two changes, tested here:

  * the board has an id of its own, which no simulator or capture uses;
  * a signature failure says what it can honestly say about why. The server
    cannot know that "the device was enrolled by a different key". It can say
    that the record does not verify under the key enrolled for that id, and
    that a freshly wiped board (seq 1, or seq at or below what the server
    already holds) failing its signature is the typical symptom of a database
    that enrolled this id with another key. That goes in a separate `hint`,
    in the ingest response and on the rejects row; the reason stays
    "bad signature", and check 2 is not weakened.
"""
import re
import time
from pathlib import Path

from backend import checks, db

from .conftest import DEV, KEY
from .helpers import digest, enrol, record, trip

ROOT = Path(__file__).resolve().parents[2]
OTHER = b"a-key-the-board-made-for-itself!"


def board_id():
    src = (ROOT / "src/main.cpp").read_text(encoding="utf-8")
    m = re.search(r"#define AC_DEVICE_ID (0x[0-9A-Fa-f]+)", src)
    assert m, "src/main.cpp defines AC_DEVICE_ID"
    return int(m.group(1), 16)


def test_the_board_id_is_not_a_demo_id():
    bid = board_id()
    assert bid not in (0x26232001, 0x26232002, 0x26232003)
    used = set()
    for f in ["native/main.cpp", "tools/dump.cpp", "tools/fleet.cpp", "tools/selftest.cpp",
              "tools/clockgate.cpp", "tools/demo_full.py", "backend/feed_sim.py"]:
        text = (ROOT / f).read_text(encoding="utf-8")
        used |= {int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{8}", text)}
        used |= {int(x) for x in re.findall(r"\b6398\d{5}\b", text)}
    assert bid not in used
    for line in (ROOT / "tools/seed/fleet.seed.capture").read_text().splitlines():
        if line.startswith("K "):
            assert int(line.split()[1]) != bid
    hil = (ROOT / "docs/HIL.md").read_text(encoding="utf-8")
    assert f"{bid:08X}" in hil and str(bid) in hil      # documented in step 1


def test_a_wiped_board_under_a_demo_key_gets_the_reason_and_a_hint(client, auth):
    enrol(client, auth, DEV, KEY)                    # the demo enrolled this id
    r = client.post("/api/ingest",
                    json={"records": [record(OTHER, DEV, 1, int(time.time()) - 60).hex()]}).json()
    assert r["accepted"] == 0
    assert r["reason"] == "bad signature"            # check 2's reason, unchanged
    hint = r["hint"]
    assert "does not verify under the key enrolled" in hint
    assert "make clean" in hint and "rotate" in hint
    row = db.rejects(1)[0]
    assert row["reason"] == "bad signature" and row["hint"] == hint


def test_a_record_altered_mid_chain_gets_no_demo_database_hint(client, auth):
    enrol(client, auth, DEV, KEY)
    recs = trip(KEY, DEV, 3)
    assert client.post("/api/ingest", json={"records": [x.hex() for x in recs]}).json()["accepted"] == 3
    bad = bytearray(record(KEY, DEV, 4, int(time.time()) - 30, prev=digest(recs[-1])))
    bad[12] ^= 1                                     # changed after signing
    r = client.post("/api/ingest", json={"records": [bytes(bad).hex()]}).json()
    assert r["reason"] == "bad signature"
    assert "does not verify under the key enrolled" in r["hint"]
    assert "make clean" not in r["hint"]             # seq 4 follows on: not the wiped-board shape


def test_other_refusals_carry_no_hint(client, auth):
    enrol(client, auth, DEV, KEY)
    r = client.post("/api/ingest",
                    json={"records": [record(KEY, DEV, 5, int(time.time())).hex()]}).json()
    assert "sequence gap" in r["reason"] and r.get("hint") is None


def test_check_2_itself_is_unchanged():
    ok, *_, reason = checks.Verifier().check(record(OTHER, DEV, 1, 1790400000), KEY, False,
                                             now=1790400060)
    assert not ok and reason == "bad signature"
