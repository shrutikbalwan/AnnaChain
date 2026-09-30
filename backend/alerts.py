"""AnnaChain — the alert rules.

An alert has to be worth waking a driver for. Two principles:

  * Say what to do, not what happened. "Reefer has been above 8 C for 35
    minutes" beats "temperature anomaly detected".
  * Do not repeat yourself. One excursion is one alert, not one per reading.

Cross-node disagreement lives here too: when two nodes on the same truck
disagree by more than the sensors' combined tolerance, the odd one out is
called a suspect sensor, not spoilage. That distinction is the whole reason
the deck can claim self-diagnosis.
"""
import time

# how long a condition must hold before it is worth telling someone
EXCURSION_MIN = 3          # consecutive readings (15 minutes at 5-minute sampling)
ETHYLENE_RISE = 1.30       # 30% above this trip's own opening baseline
ETHYLENE_MIN_DELTA = 5     # ...and at least this many ppb, so noise cannot trip it
ETHYLENE_BASELINE_N = 12   # readings used to establish the baseline
BATTERY_LOW_PCT = 20
DISAGREE_C = 1.5           # two SHT40s should agree far better than this
DISAGREE_RUNS = 3          # consecutive buckets before a node is called suspect
SILENT_ALERT_S = 60        # real seconds with no ingest before we say a node is quiet


class AlertEngine:
    def __init__(self, db):
        self.db = db
        self.state = {}            # device -> per-device running state
        self.truck_state = {}      # truck -> disagreement run lengths
        self.suspect = set()       # devices whose readings are not to be trusted

    def _st(self, dev):
        return self.state.setdefault(dev, {
            "hot_run": 0, "cold_run": 0, "open": set(),
            "c2h4_base": [], "baseline": None, "silent_since": None,
        })

    def _raise(self, dev, seq, ts, kind, severity, message, once=True):
        st = self._st(dev)
        if once and kind in st["open"]:
            return False
        st["open"].add(kind)
        self.db.add_alert(dev, seq, ts, kind, severity, message)
        return True

    def _clear(self, dev, kind):
        self._st(dev)["open"].discard(kind)

    # ── per record ───────────────────────────────────────────────────────
    def on_record(self, r, shipment):
        dev, seq, ts = r["device"], r["seq"], r["ts"]
        st = self._st(dev)
        lo = shipment["min_c"] if shipment else 2.0
        hi = shipment["max_c"] if shipment else 8.0

        # the sensor said it could not measure — that is a device fault,
        # and it must never be read as a cold-chain event
        if r["sensor_bad"]:
            self._raise(dev, seq, ts, "sensor_fault", "high",
                        "Sensor did not answer. This reading is not a measurement "
                        "— check the wiring before trusting anything around it.")
            return
        self._clear(dev, "sensor_fault")

        t = r["temp_c"]

        # A node the truck has voted against does not get to raise a cold-chain
        # alarm. Its readings are still stored and still signed — they are just
        # not treated as evidence about the load.
        if dev in self.suspect:
            return

        # temperature, above
        if t > hi:
            st["hot_run"] += 1
            if st["hot_run"] == EXCURSION_MIN:
                mins = EXCURSION_MIN * 5
                self._raise(dev, seq, ts, "temp_high", "high",
                            f"{t:.1f} °C — above the {hi:.0f} °C limit for "
                            f"{mins} minutes. The load can still be saved if the "
                            f"reefer is checked now.")
        else:
            if st["hot_run"] >= EXCURSION_MIN:
                self._raise(dev, seq, ts, "temp_back", "info",
                            f"Back inside range at {t:.1f} °C.", once=False)
                self._clear(dev, "temp_high")
            st["hot_run"] = 0

        # temperature, below — a frozen load is damaged just as surely
        if t < lo:
            st["cold_run"] += 1
            if st["cold_run"] == EXCURSION_MIN:
                self._raise(dev, seq, ts, "temp_low", "high",
                            f"{t:.1f} °C — below the {lo:.0f} °C limit. "
                            f"Chill injury starts here, and it is not reversible.")
        else:
            if st["cold_run"] >= EXCURSION_MIN:
                self._clear(dev, "temp_low")
            st["cold_run"] = 0

        # Ethylene, if a sensor is fitted at all.
        #
        # An absolute ppb threshold is meaningless across commodities, and we
        # will not pretend to a calibrated number we cannot yet measure. What is
        # defensible is a rise against this consignment's own opening baseline:
        # the load is ripening faster than it was.
        e = r["c2h4_ppb"]
        if e is not None:
            if st["baseline"] is None:
                st["c2h4_base"].append(e)
                if len(st["c2h4_base"]) >= ETHYLENE_BASELINE_N:
                    b = sorted(st["c2h4_base"])
                    st["baseline"] = b[len(b) // 2]
            else:
                base = st["baseline"]
                if e >= base * ETHYLENE_RISE and e - base >= ETHYLENE_MIN_DELTA:
                    pct = round((e / base - 1) * 100)
                    self._raise(dev, seq, ts, "ethylene", "medium",
                                f"Ethylene up {pct}% on this trip's baseline "
                                f"({base} → {e} ppb). The load is ripening faster "
                                f"than when it was loaded — shelf life is shortening.")

        # tamper — never auto-clears, someone has to look
        if r["tamper"]:
            self._raise(dev, seq, ts, "tamper", "high",
                        "Enclosure opened in transit. Custody is in question "
                        "from this record onward.")

        # battery
        if r["battery_pct"] < BATTERY_LOW_PCT:
            self._raise(dev, seq, ts, "battery", "medium",
                        f"Battery at {r['battery_pct']}%. Solar is not keeping up "
                        f"— the node may be in a dark container.")

    # ── calibration has lapsed (check 7) ─────────────────────────────────
    def on_stale_calibration(self, dev, cal_date, cal_due):
        import time as _t
        days = int((_t.time() - cal_due) / 86400) if cal_due else 0
        self._raise(dev, None, None, "calibration", "medium",
                    f"This sensor's verification lapsed {days} days ago. EN 13486 "
                    f"wants it re-checked against a reference before its readings "
                    f"are presented as verified. The readings are still recorded "
                    f"\u2014 they are just not certified.")

    # ── on a declared gap ────────────────────────────────────────────────
    def on_gap(self, dev, from_seq, to_seq):
        n = to_seq - from_seq + 1
        self._raise(dev, to_seq, None, f"gap_{from_seq}", "high",
                    f"The node declared {n} records lost (sequence {from_seq}"
                    f"–{to_seq}): the outage outlasted its flash. The hole is "
                    f"recorded, not hidden.", once=False)

    # ── on recovery after silence ────────────────────────────────────────
    def on_recovery(self, dev, n, silent_s):
        self._raise(dev, None, None, "recovered", "info",
                    f"Back online after {fmt_dur(silent_s)}. {n} records that were "
                    f"held on the device have been recovered, in order.",
                    once=False)

    # ── cross-node self-diagnosis ────────────────────────────────────────
    #
    # This is the claim on slide 2 that is easiest to get wrong. A node whose
    # sensor has drifted will read 12 °C in a 4 °C truck, and the naive system
    # shouts "spoilage" and sends a driver to check a load that is perfectly
    # fine. Do that twice and nobody trusts the alerts again.
    #
    # With three or more nodes the majority is the load and the outlier is the
    # sensor. With two, all you can honestly say is that they disagree.
    #
    # One disagreeing reading is noise, so a node is only called suspect after
    # DISAGREE_RUNS consecutive time buckets. Once it is, its temperature alerts
    # are held back — which is the whole point.

    def diagnose(self, truck, bucket, readings):
        """readings: {device_id: temp_c} from one time bucket on one truck.

        Returns the device now considered suspect, if that changed."""
        if len(readings) < 2:
            return None

        st = self.truck_state.setdefault(truck, {"run": {}, "last_bucket": None})
        if st["last_bucket"] == bucket:
            return None
        st["last_bucket"] = bucket

        items = sorted(readings.items(), key=lambda kv: kv[1])
        lo_dev, lo_t = items[0]
        hi_dev, hi_t = items[-1]

        if hi_t - lo_t < DISAGREE_C:
            st["run"].clear()
            return None

        if len(readings) >= 3:
            mid = items[len(items) // 2][1]
            odd, odd_t = (hi_dev, hi_t) if abs(hi_t - mid) > abs(lo_t - mid) \
                                        else (lo_dev, lo_t)
            if abs(odd_t - mid) < DISAGREE_C:
                st["run"].clear()
                return None

            runs = st["run"]
            runs[odd] = runs.get(odd, 0) + 1
            for d in list(runs):
                if d != odd:
                    runs.pop(d)

            if runs[odd] == DISAGREE_RUNS:
                self.suspect.add(odd)
                others = ", ".join(f"{v:.1f}" for k, v in readings.items() if k != odd)
                self._raise(odd, None, bucket, "suspect_sensor", "medium",
                            f"This node reads {odd_t:.1f} \u00b0C while the others on "
                            f"the same truck read {others} \u00b0C. Flagging it as a "
                            f"suspect sensor \u2014 not reporting spoilage. Its "
                            f"temperature alerts are held until it agrees again.")
                return odd
            return None

        # exactly two nodes: report the disagreement, name no culprit
        runs = st["run"]
        key = (lo_dev, hi_dev)
        runs[key] = runs.get(key, 0) + 1
        if runs[key] == DISAGREE_RUNS:
            self._raise(hi_dev, None, bucket, "disagree", "medium",
                        f"Two nodes on this truck disagree by {hi_t - lo_t:.1f} "
                        f"\u00b0C and have done for {DISAGREE_RUNS} readings. A third "
                        f"node would say which one is wrong.")
        return None

    def clear_suspect(self, device):
        if device in self.suspect:
            self.suspect.discard(device)
            self._clear(device, "suspect_sensor")
            self._raise(device, None, None, "sensor_agrees", "info",
                        "This node agrees with the others again. Its temperature "
                        "alerts are live once more.", once=False)


def fmt_dur(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return f"{seconds} seconds"
    if seconds < 5400:
        return f"{seconds // 60} minutes"
    h, m = divmod(seconds // 60, 60)
    return f"{h} h {m:02d} min"
