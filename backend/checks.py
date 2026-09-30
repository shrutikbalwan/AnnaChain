"""AnnaChain — the eight checks, and the record format they run on.

This is the slide-3 claim in executable form: nothing reaches the database
until all eight pass. A record that fails is not silently dropped either — the
reason is written to `rejects`, because an unexplained refusal is its own kind
of missing data.

The layout here must match lib/ac/ac_record.h byte for byte. tools/dump.cpp
exists to prove it does.
"""
import hashlib, hmac, struct, time

REC = 84
BODY = 52

# ── the format version (docs/CRYPTO.md) ────────────────────────────────────
# v1 is the 84-byte record above. It has no version byte, and never will: its
# bytes are the evidence, and every node, capture and database already holds
# them. It is identified by its LENGTH, never by its first byte (which is the
# low byte of the device id, and can be anything).
#
# Every later format starts with a version byte, 2 or more, covered by the
# signature, and has a fixed length of its own that is never 84. This server
# knows only v1; anything else is refused by name.
FORMAT_V1 = 1
FORMATS = {FORMAT_V1: REC}      # version -> its fixed length


def record_format(raw: bytes):
    """The format version of a record, or None if it is not one we know."""
    if len(raw) == REC:
        return FORMAT_V1
    if raw and raw[0] >= 2 and FORMATS.get(raw[0]) == len(raw):
        return raw[0]
    return None


def format_refusal(raw: bytes) -> str:
    """Why record_format() said None, in words a log reader can act on."""
    if not raw:
        return "empty record"
    return (f"not a record in any format this server knows: {len(raw)} bytes, "
            f"record format version {raw[0]} by its first byte (v1 is exactly "
            f"{REC} bytes with no version byte; see docs/CRYPTO.md)")

# Check 5. A record's time has to be possible for the device that sent it.
MAX_SKEW_S = 60                 # ahead of the server by more than this is a wrong clock
MAX_HOLD_S = 30 * 86400         # older than a node (14 days of flash) plus a gateway
                                # buffer could have held it is a wrong clock too

FLAG_TAMPER, FLAG_MOVED, FLAG_CHARGING = 1, 2, 4
FLAG_COLD, FLAG_SELFTEST, FLAG_SENSORBAD = 8, 16, 32

ETHYLENE_NOT_FITTED = 0xFFFF
SENSOR_MIN_C, SENSOR_MAX_C = -40.0, 125.0      # SHT40's own rated range


def parse(raw: bytes) -> dict:
    dev, seq, ts, temp, rh, c2h4, flags, batt = struct.unpack_from("<IIIhHHBB", raw)
    return {
        "device": dev, "seq": seq, "ts": ts,
        "temp_c": temp / 100.0,
        "rh_pct": rh / 100.0,
        "c2h4_ppb": None if c2h4 == ETHYLENE_NOT_FITTED else c2h4,
        "flags": flags,
        "battery_pct": batt,
        "tamper":    bool(flags & FLAG_TAMPER),
        "moved":     bool(flags & FLAG_MOVED),
        "charging":  bool(flags & FLAG_CHARGING),
        "cold":      bool(flags & FLAG_COLD),
        "selftest":  bool(flags & FLAG_SELFTEST),
        "sensor_bad": bool(flags & FLAG_SENSORBAD),
        "prev": raw[20:52].hex(),
        "sig":  raw[52:84].hex(),
    }


def digest(raw: bytes) -> bytes:
    """SHA-256 of everything except the signature — what the next record chains to."""
    return hashlib.sha256(raw[:BODY]).digest()


def gap_digest(device: int, from_seq: int, to_seq: int) -> bytes:
    """What the node signs to declare records lost: gapDigest() in ac_record.cpp."""
    return hashlib.sha256(f"ACGAP|{device}|{from_seq}|{to_seq}".encode()).digest()


def gap_mac_ok(key: bytes, device: int, from_seq: int, to_seq: int, mac_hex) -> bool:
    if key is None or not mac_hex:
        return False
    try:
        mac = bytes.fromhex(mac_hex)
    except ValueError:
        return False
    want = hmac.new(key, gap_digest(device, from_seq, to_seq), hashlib.sha256).digest()
    return hmac.compare_digest(want, mac)


def merkle_root(leaves) -> bytes:
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest()
                 for i in range(0, len(level), 2)]
    return level[0]


class Verifier:
    """Holds the per-device chain tip. One instance per process."""

    def __init__(self):
        self.tip = {}          # device -> digest bytes of the last accepted record
        self.ack = {}          # device -> last accepted sequence number
        self.anchor_next = {}  # device -> accept the next record as a fresh anchor
        self.stale_calibration = set()   # devices past their EN 13486 due date
        self.last_ts = {}      # device -> timestamp of the last accepted record

    def load(self, device_id, last_ack, tip_hex, anchor_next, last_ts=None):
        self.ack[device_id] = last_ack or 0
        self.tip[device_id] = bytes.fromhex(tip_hex) if tip_hex else None
        self.anchor_next[device_id] = bool(anchor_next)
        if last_ts:
            self.last_ts[device_id] = last_ts

    def note_gap(self, device_id, to_seq):
        """The node told us records are gone. Re-anchor rather than refuse for ever."""
        self.ack[device_id] = to_seq
        self.tip[device_id] = None
        self.anchor_next[device_id] = True

    def check(self, raw: bytes, key: bytes, seen_before: bool, cal_due=None, now=None):
        """Returns (ok, parsed, digest_bytes, reason).

        cal_due: unix time by which this device's calibration had to be renewed,
        or None if no calibration has been recorded for it.
        now: the server's clock; the real one unless a test supplies it."""
        now = time.time() if now is None else now
        r = parse(raw)
        d = digest(raw)
        dev, seq = r["device"], r["seq"]

        # 1 — is the device registered?
        if key is None:
            return False, r, d, "unknown device"

        # 2 — is the signature valid?
        #     HMAC today; ECDSA over the same digest once the ATECC608B is fitted.
        want = hmac.new(key, d, hashlib.sha256).digest()
        if not hmac.compare_digest(want, bytes.fromhex(r["sig"])):
            return False, r, d, "bad signature"

        have = self.ack.get(dev, 0)

        # 3 — is the sequence correct?
        # 4 — duplicate or replay?
        if seq <= have or seen_before:
            return False, r, d, "duplicate or replay"
        if seq != have + 1:
            return False, r, d, f"sequence gap (expected {have + 1})"

        # 5 — is the timestamp sensible?
        #     Possible at all; not ahead of us; not older than the device could
        #     have held it; and not earlier than the reading before it. A node
        #     whose clock started a year wrong fails the third rule on its very
        #     first record, which is the point.
        ts = r["ts"]
        if not (1600000000 < ts < 2200000000):
            return False, r, d, "impossible timestamp"
        if ts > now + MAX_SKEW_S:
            return False, r, d, (f"timestamp ahead of the server clock by "
                                 f"{int(ts - now)} s (allowed {MAX_SKEW_S} s)")
        if ts < now - MAX_HOLD_S:
            return False, r, d, (f"timestamp older than a node can hold a reading "
                                 f"({int((now - ts) // 86400)} days; allowed "
                                 f"{MAX_HOLD_S // 86400}) \u2014 is the device clock wrong?")
        last = self.last_ts.get(dev)
        if last is not None and ts < last:
            return False, r, d, (f"timestamp went backwards ({int(last - ts)} s before "
                                 f"the previous reading)")

        # 6 — is the hash chain unbroken?
        tip = self.tip.get(dev)
        if tip is not None:
            if bytes.fromhex(r["prev"]) != tip:
                return False, r, d, "broken hash chain"
        elif not self.anchor_next.get(dev) and have > 0:
            return False, r, d, "no chain anchor"

        # 7 — is the calibration still valid?
        #
        # EN 13486 requires periodic verification of cold-chain recorders. A
        # reading from a sensor whose verification has lapsed is still a real
        # reading and is still stored — refusing it would throw away the only
        # record of the journey — but it is marked, and it must never be
        # presented to a buyer as a verified measurement.
        if cal_due is not None and r["ts"] > cal_due:
            self.stale_calibration.add(dev)
        else:
            self.stale_calibration.discard(dev)

        # 8 — is the sensor healthy?
        #
        # A record the node flagged as a sensor fault is ACCEPTED, not refused.
        # It is a correctly signed statement that the sensor failed at that
        # instant, and it is worth keeping. Refusing it would also stall the
        # sequence for ever, because the node has nothing else to send in its
        # place. It raises an alert instead (see alerts.py).
        #
        # What is refused is a reading that claims to be fine and is physically
        # impossible for the part — that is corruption, not a measurement.
        if not r["sensor_bad"] and not (SENSOR_MIN_C <= r["temp_c"] <= SENSOR_MAX_C):
            return False, r, d, "outside the sensor's rated range"

        self.tip[dev] = d
        self.ack[dev] = seq
        self.anchor_next[dev] = False
        self.last_ts[dev] = ts
        return True, r, d, None


# ── re-deriving a stored chain ─────────────────────────────────────────────
#
# This is what "verify" means. The stored columns are not trusted for anything:
# each record's digest, signature, link to the record before, and every parsed
# column are recomputed from the 84 bytes the device sent. Any disagreement is
# tampering, and the kind is reported, because a buyer deserves to know whether
# a reading was forged or only the display of it was doctored.

COLUMNS = ("device_id", "seq", "ts", "temp_c", "rh_pct", "c2h4_ppb", "flags",
           "batt_pct", "digest", "prev", "sig")


def _key_at(keys, seq):
    """keys: [(from_seq, key)] oldest first. The key that signed record seq."""
    k = None
    for from_seq, key in keys:
        if from_seq <= seq:
            k = key
    return k


def _mismatched_columns(row, p, d):
    want = {"device_id": p["device"], "seq": p["seq"], "ts": p["ts"],
            "temp_c": p["temp_c"], "rh_pct": p["rh_pct"], "c2h4_ppb": p["c2h4_ppb"],
            "flags": p["flags"], "batt_pct": p["battery_pct"],
            "digest": d.hex(), "prev": p["prev"], "sig": p["sig"]}
    bad = []
    for col in COLUMNS:
        have, w = row[col], want[col]
        if isinstance(w, float) or isinstance(have, float):
            if have is None or w is None or abs(float(have) - float(w)) > 1e-6:
                bad.append(col)
        elif have != w:
            bad.append(col)
    return bad


def verify_chain(rows, keys, gaps, start=None):
    """Walk stored records and re-derive everything from their raw bytes.

    rows:  stored rows, oldest first, with the COLUMNS plus `raw`.
    keys:  [(from_seq, key bytes)] for this device, oldest first.
    gaps:  declared gaps, each with from_seq, to_seq and the device's mac.
    start: (seq, digest) of an already-verified record to resume after.

    Returns a dict: ok, broken_at, reason, detail, columns, digests, checked.
    reason is one of bad_signature, column_mismatch, chain_break, missing_raw,
    bad_gap_signature."""
    out = {"ok": True, "broken_at": None, "reason": None, "detail": None,
           "columns": [], "digests": [], "checked": 0}

    def fail(seq, reason, detail, columns=()):
        out.update(ok=False, broken_at=seq, reason=reason, detail=detail,
                   columns=list(columns))
        return out

    # A gap excuses missing records only if the device signed it.
    honoured = {}                       # to_seq -> from_seq
    for g in sorted(gaps, key=lambda g: g["from_seq"]):
        dev = rows[0]["device_id"] if rows else None
        if dev is None or not gap_mac_ok(_key_at(keys, g["from_seq"]), dev,
                                         g["from_seq"], g["to_seq"], g["mac"]):
            return fail(g["from_seq"], "bad_gap_signature",
                        f"records {g['from_seq']}\u2013{g['to_seq']} are recorded as "
                        f"lost, but the device did not sign that notice")
        honoured[g["to_seq"]] = g["from_seq"]

    prev_seq, prev_d = start if start else (0, None)
    for row in rows:
        seq, raw = row["seq"], row["raw"]
        if raw is None or len(raw) != REC:
            return fail(seq, "missing_raw",
                        "the device's original bytes were not kept, so this reading "
                        "cannot be checked")
        raw = bytes(raw)
        p = parse(raw)
        d = digest(raw)

        key = _key_at(keys, p["seq"])
        want = hmac.new(key, d, hashlib.sha256).digest() if key else None
        if want is None or not hmac.compare_digest(want, raw[BODY:REC]):
            return fail(seq, "bad_signature",
                        "the signed bytes were changed after the device signed them")

        bad = _mismatched_columns(row, p, d)
        if bad:
            return fail(seq, "column_mismatch",
                        "the stored values differ from what the device signed: "
                        + ", ".join(bad), bad)

        after_gap = honoured.get(seq - 1) == prev_seq + 1
        if seq != prev_seq + 1 and not after_gap:
            return fail(seq, "chain_break",
                        f"readings {prev_seq + 1}\u2013{seq - 1} are missing and "
                        f"were never declared lost" if seq > prev_seq + 1 else
                        "readings are out of order")
        if not after_gap and prev_d is not None and raw[20:52] != prev_d:
            return fail(seq, "chain_break",
                        f"this reading does not follow reading {prev_seq}")

        out["digests"].append(d)
        out["checked"] += 1
        prev_seq, prev_d = seq, d
    out["last"] = (prev_seq, prev_d)
    return out
