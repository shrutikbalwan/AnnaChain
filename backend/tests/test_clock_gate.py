"""The P1 gate: a board flashed today, run forward past 30 days of uptime,
every record accepted.

tools/clockgate.cpp is built and run: the real node code (ac_node.cpp) with the
clock src/main.cpp gives it, starting at this build's kClockBase, no server for
its first two days, a 2-day outage, a power cut and reboot at day 32.5, 36 days
in all. What it delivers is fed to the real server through /api/ingest, with
the server's clock (app.time) set to the simulated true time of each delivery,
and one server restart on the way (clock state must survive it). Nothing in
check 5 is relaxed for this: MAX_HOLD_S, MAX_SKEW_S and the monotonic rule are
the production values.

Before the P1 fix this could not pass: every record after 30 days past the
compiled-in date was refused, and a reboot sent time backwards.

Skipped when no C++ compiler is available.
"""
import shutil
import subprocess
import time as real_time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import app as app_mod, checks, db

from .helpers import login

ROOT = Path(__file__).resolve().parents[2]
CORE = ["lib/ac/ac_sha256.cpp", "lib/ac/ac_record.cpp", "lib/ac/ac_node.cpp",
        "lib/ac/ac_gateway.cpp", "lib/ac/ac_sim.cpp"]


@pytest.fixture(scope="module")
def capture(tmp_path_factory):
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        pytest.skip("no C++ compiler")
    exe = tmp_path_factory.mktemp("gate") / "clockgate"
    subprocess.run([cxx, "-std=gnu++17", "-O2", "-DAC_LOG_CAPACITY=4096", "-Ilib/ac", *CORE,
                    "tools/clockgate.cpp", "-o", str(exe)], cwd=ROOT, check=True)
    out = subprocess.run([str(exe)], cwd=ROOT, check=True, capture_output=True, text=True)
    return [l for l in out.stdout.splitlines() if l.strip()]


class ServerClock:
    """app.time with time() under the test's control; everything else real."""
    def __init__(self):
        self.t = real_time.time()

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(real_time, name)


def test_a_board_flashed_today_runs_past_30_days(client, capture, monkeypatch):
    clock = ServerClock()
    monkeypatch.setattr(app_mod, "time", clock)
    auth = login(client)

    sent, accepted, flagged, first_set, refusals = 0, 0, 0, None, []
    restarted, oldest_lag = False, 0
    for line in capture:
        if line.startswith("#"):
            print(line)
            continue
        if line.startswith("K "):
            _, dev, key = line.split()
            device = int(dev)
            r = client.post("/api/register", headers=auth,
                            json={"device": device, "key_hex": key, "label": "gate"})
            assert r.status_code == 200, r.text
            continue
        _, hexrec, at = line.split()
        clock.t = float(at)
        p = checks.parse(bytes.fromhex(hexrec))
        if p["time_unset"]:
            flagged += 1
        elif first_set is None:
            first_set = p["seq"]
        oldest_lag = max(oldest_lag, int(at) - p["ts"])
        if not restarted and p["seq"] > 20 * 288:          # a server restart, mid-trip
            app_mod.verifier.__init__()
            with TestClient(app_mod.app):                  # startup reloads from the db
                pass
            restarted = True
        res = client.post("/api/ingest", json={"records": [hexrec]}).json()
        sent += 1
        accepted += res["accepted"]
        if not res["accepted"]:
            refusals.append((p["seq"], res["reason"]))

    d = db.device(device)
    uptime_days = (d["last_ts"] - (int(capture[1].split("=")[1].split(",")[0]))) / 86400
    print(f"gate: sent {sent}, accepted {accepted}, refused {len(refusals)}; "
          f"{flagged} flagged FLAG_TIMEUNSET (before first contact), first wall-clock "
          f"record seq {first_set}; longest delivery lag {oldest_lag / 3600:.1f} h; "
          f"server restarted mid-trip: {restarted}; device clock_set = {d['clock_set']}; "
          f"last record stamped {uptime_days:.1f} days after kClockBase")
    assert refusals == [], refusals[:5]
    assert accepted == sent == 36 * 288
    assert flagged == 2 * 288 + 1 and first_set == 2 * 288 + 2
    assert d["last_ack"] == sent and d["clock_set"] == 1
    v = client.get(f"/api/verify/{device}?full=true").json()
    assert v["ok"] is True and v["records"] == sent
