"""A declared gap on the real data path: node -> LoRa -> truck gateway -> server.

The C++ node and gateway are built and run (tools/dump.cpp --gateway). The
node's flash overruns while the crate is out of LoRa range; its signed gap
notice waits in the gateway's buffer while the cab has no signal, then goes up
in its place in the queue. This test replays exactly what the gateway sent into
the server and reads what the buyer is shown.

Skipped when no C++ compiler is available.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from .helpers import login

ROOT = Path(__file__).resolve().parents[2]
CORE = ["lib/ac/ac_sha256.cpp", "lib/ac/ac_record.cpp", "lib/ac/ac_node.cpp",
        "lib/ac/ac_gateway.cpp", "lib/ac/ac_sim.cpp"]
DEV = 0x26232001
SHIP = f"AC-{DEV:08X}"


@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        pytest.skip("no C++ compiler")
    exe = tmp_path_factory.mktemp("gw") / "dump"
    subprocess.run([cxx, "-std=gnu++17", "-DAC_LOG_CAPACITY=4096", "-Ilib/ac", *CORE,
                    "tools/dump.cpp", "-o", str(exe)], cwd=ROOT, check=True)
    out = subprocess.run([str(exe), "--gateway", "50", "4200"], cwd=ROOT, check=True,
                         capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


def replay(client, lines, tamper_gap=None):
    """Feed the gateway's uplink traffic to the server, in order, as feed_sim does."""
    auth = login(client)
    pending, results = [], {"gaps": []}

    def flush():
        for i in range(0, len(pending), 20):
            r = client.post("/api/ingest", json={"records": pending[i:i + 20]})
            assert r.status_code == 200
        pending.clear()

    for line in lines:
        if line.startswith("K "):
            _, dev, key = line.split()
            assert client.post("/api/register", headers=auth,
                               json={"device": int(dev), "key_hex": key}).status_code == 200
        elif line.startswith("R "):
            pending.append(line[2:])
        elif line.startswith("G "):
            flush()
            _, dev, lo, hi, mac = line.split()
            if tamper_gap:
                lo, hi, mac = tamper_gap(int(lo), int(hi), mac)
            r = client.post("/api/gap", json={"device": int(dev), "from_seq": int(lo),
                                              "to_seq": int(hi), "mac": mac})
            results["gaps"].append(r.status_code)
    flush()
    return results


def test_the_capture_went_through_the_gateway(capture):
    gaps = [l for l in capture if l.startswith("G ")]
    assert len(gaps) == 1 and gaps[0].split()[2:4] == ["51", "154"]
    # the notice goes up before the records that follow the hole
    i = capture.index(gaps[0])
    assert all(not l.startswith("R ") or int.from_bytes(bytes.fromhex(l[2:])[4:8], "little") > 154
               for l in capture[i:])


def test_buyer_sees_a_declared_hole_through_the_gateway(client, capture):
    res = replay(client, capture)
    assert res["gaps"] == [200]
    v = client.get(f"/api/verify/{DEV}?full=true").json()
    assert v["ok"] is True, v
    assert v["declared_gaps"] == [{"from": 51, "to": 154}]
    assert v["records"] == 50 + 4108
    t = client.get(f"/api/trace/{SHIP}").json()
    assert t["verdict"] == "Complete, with a declared hole"
    assert t["declared_lost"] == 104 and t["chain_ok"] is True


def test_a_gateway_that_widens_the_hole_is_refused(client, capture):
    """A compromised gateway tries to swallow twenty more readings."""
    res = replay(client, capture, tamper_gap=lambda lo, hi, mac: (lo, hi + 20, mac))
    assert res["gaps"] == [401]
    t = client.get(f"/api/trace/{SHIP}").json()
    assert t["declared_lost"] == 0
    # The server kept waiting for record 51, so nothing after the hole was taken.
    assert t["readings"] == 50
