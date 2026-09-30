"""P6: the LoRa airtime and the power model are calculated, and say so.

tools/airtime.py is the one place the time-on-air numbers in docs/HIL.md step 5,
docs/CRYPTO.md and docs/POWER.md come from; tools/power.py takes the deck's
inputs by name and has no defaults for them.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import airtime  # noqa: E402
import power    # noqa: E402


def test_the_documented_airtimes_are_what_the_formula_gives():
    assert round(airtime.airtime_s(86), 2) == 0.66       # record, HIL step 5 and CRYPTO.md
    assert round(airtime.airtime_s(2 + 117), 2) == 0.86  # a v2 record, CRYPTO.md
    assert round(airtime.airtime_s(6), 2) == 0.14        # query
    assert round(airtime.airtime_s(15), 2) == 0.20       # last-ACK + time
    assert round(airtime.airtime_s(10), 2) == 0.17       # ACK
    node, gw = airtime.per_sample()
    assert round(node, 2) == 0.80 and round(gw, 2) == 0.37


def test_the_docs_say_calculated():
    hil = (ROOT / "docs/HIL.md").read_text(encoding="utf-8")
    assert "CALCULATED" in hil and "tools/airtime.py" in hil
    assert "CALCULATED" in (ROOT / "docs/POWER.md").read_text(encoding="utf-8")


def test_the_power_script_has_no_defaults_for_the_decks_inputs():
    with pytest.raises(SystemExit):
        power.main(["--cell-mah", "1000", "--usable", "1"])


def test_idle_alone_at_36_ua_needs_about_1200_mah_for_3_8_years(capsys):
    power.main(["--implied", "3.8", "--cell-mah", "1000", "--usable", "1"])
    assert "30.0" in capsys.readouterr().out            # 30 µA per usable 1000 mAh
    power.main(["--cell-mah", "1200", "--usable", "1", "--sample-s", "300", "--sleep-ua", "36",
                "--active-ma", "0", "--active-s", "0", "--tx-ma", "0", "--tx-s", "0",
                "--rx-ma", "0", "--rx-s", "0"])
    out = capsys.readouterr().out
    assert "36.0" in out and "3.80 years" in out
