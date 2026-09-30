#!/usr/bin/env python3
"""Battery life of a crate node, from NAMED inputs. See docs/POWER.md.

    python tools/power.py --cell-mah <C> --usable <f> --sample-s <T> \\
        --sleep-ua <I> --active-ma <I> --active-s <t> \\
        --tx-ma <I> [--tx-s <t>] --rx-ma <I> --rx-s <t> [--self-discharge-pct-yr <p>]

Every input that decides the answer must be given: there are no defaults for
the deck's values, on purpose. The one default is --tx-s, the LoRa time on air
per sample from tools/airtime.py, which is CALCULATED, not measured. The
result is a calculation too, until docs/POWER.md's measurement has been run.

    python tools/power.py --implied 3.8 --cell-mah <C> --usable <f>

prints the average current a claimed life needs from a given cell, which is
the fastest way to check a slide.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from airtime import per_sample  # noqa: E402

HOURS_PER_YEAR = 8766                    # 365.25 days


def average_ua(a) -> float:
    """Mean current in µA over one sample period."""
    awake_s = a.active_s + a.tx_s + a.rx_s
    charge_uas = (a.sleep_ua * max(a.sample_s - awake_s, 0)
                  + a.active_ma * 1000 * a.active_s
                  + a.tx_ma * 1000 * a.tx_s
                  + a.rx_ma * 1000 * a.rx_s)
    return charge_uas / a.sample_s


def life_years(a) -> float:
    usable_uah = a.cell_mah * 1000 * a.usable
    loss_uah_per_h = a.cell_mah * 1000 * a.self_discharge_pct_yr / 100 / HOURS_PER_YEAR
    return usable_uah / (average_ua(a) + loss_uah_per_h) / HOURS_PER_YEAR


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cell-mah", type=float, required=True, help="cell capacity, mAh")
    ap.add_argument("--usable", type=float, required=True,
                    help="fraction of it usable before the node browns out (0-1)")
    ap.add_argument("--implied", type=float, help="print the mean current this life needs, years")
    ap.add_argument("--sample-s", type=float, help="seconds between samples")
    ap.add_argument("--sleep-ua", type=float, help="whole-node current asleep, µA")
    ap.add_argument("--active-ma", type=float, help="current while awake: sensor, sign, flash, mA")
    ap.add_argument("--active-s", type=float, help="awake time per sample, excluding radio, s")
    ap.add_argument("--tx-ma", type=float, help="SX1262 transmit current at the set power, mA")
    ap.add_argument("--tx-s", type=float, default=per_sample()[0],
                    help="time on air per sample, s (default: tools/airtime.py, CALCULATED)")
    ap.add_argument("--rx-ma", type=float, help="receive current while waiting for replies, mA")
    ap.add_argument("--rx-s", type=float, help="receive window per sample, s")
    ap.add_argument("--self-discharge-pct-yr", type=float, default=0.0,
                    help="cell self-discharge, %% of capacity per year (default 0)")
    a = ap.parse_args(argv)

    if a.implied:
        ua = a.cell_mah * 1000 * a.usable / (a.implied * HOURS_PER_YEAR)
        print(f"{a.implied} years from {a.cell_mah:g} mAh x {a.usable:g} usable needs a mean of "
              f"{ua:.1f} µA, everything included (sleep, sampling, radio, self-discharge)")
        return 0
    missing = [n for n in ("sample_s", "sleep_ua", "active_ma", "active_s", "tx_ma", "rx_ma", "rx_s")
               if getattr(a, n) is None]
    if missing:
        ap.error("missing: " + ", ".join("--" + m.replace("_", "-") for m in missing))
    print(f"mean current {average_ua(a):.1f} µA -> life {life_years(a):.2f} years "
          f"(CALCULATED; tx {a.tx_s:.2f} s/sample from tools/airtime.py unless given)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
