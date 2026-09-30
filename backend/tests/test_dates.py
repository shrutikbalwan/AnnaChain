"""Things that were correct on 1 Oct 2026 and become wrong on a known date.

The P1 clock was one (25 Oct 2026). These are the others that were fixed; the
full list, with the ones left and why, is in the Phase 7 report (PR #3).

  * 18 Sep 2039 23:06:40 UTC: check 5 refused any ts >= 2,200,000,000 as
    "impossible", so from that moment every record would have been refused.
    The only real bound is the format's (uint32, 2106); "not ahead of the
    server" already refuses a future timestamp, by 60 s, not by 13 years.
  * 19 Jan 2038 03:14:08 UTC, Windows only: dump and fleet read --start with
    atol() into a `long`, which is 32 bits on Windows, so a start after that
    moment could not be given; on the team's laptop that is the demo tools.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

from backend import checks

from .conftest import DEV, KEY
from .helpers import record

ROOT = Path(__file__).resolve().parents[2]
CORE = ["lib/ac/ac_sha256.cpp", "lib/ac/ac_record.cpp", "lib/ac/ac_node.cpp",
        "lib/ac/ac_gateway.cpp", "lib/ac/ac_sim.cpp"]
AFTER_2039 = 2_200_000_100           # 18 Sep 2039, just past the old bound


def test_a_record_after_2039_is_not_impossible():
    ok, *_, reason = checks.Verifier().check(record(KEY, DEV, 1, AFTER_2039), KEY, False,
                                             now=AFTER_2039 + 5)
    assert ok, reason


def test_a_timestamp_before_2020_is_still_impossible():
    ok, *_, reason = checks.Verifier().check(record(KEY, DEV, 1, 1_500_000_000), KEY, False,
                                             now=1_500_000_000)
    assert not ok and reason == "impossible timestamp"


@pytest.mark.parametrize("tool", ["dump", "fleet"])
def test_capture_tools_take_a_start_after_2038(tool, tmp_path):
    cxx = shutil.which("g++") or shutil.which("clang++")
    if not cxx:
        pytest.skip("no C++ compiler")
    exe = tmp_path / tool
    subprocess.run([cxx, "-std=gnu++17", "-DAC_LOG_CAPACITY=4096", "-Ilib/ac", *CORE,
                    f"tools/{tool}.cpp", "-o", str(exe)], cwd=ROOT, check=True)
    start = 2_200_000_100 - 2_200_000_100 % 300
    out = subprocess.run([str(exe), "5", "1", "--start", str(start)], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout.splitlines()
    first = next(l for l in out if l.startswith("R "))
    assert checks.parse(bytes.fromhex(first[2:]))["ts"] == start
