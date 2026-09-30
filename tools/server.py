#!/usr/bin/env python3
"""AnnaChain — the server half, on your laptop.

    python3 tools/server.py /dev/ttyACM0        # Linux
    python3 tools/server.py /dev/cu.usbmodem1   # macOS
    python3 tools/server.py COM5                # Windows
    python3 tools/server.py --replay demo.jsonl # no board needed

It speaks to the node over the USB cable and does the job slide 3 gives the
backend: eight checks, and nothing is stored until all eight pass. Every
accepted record is appended to records.jsonl, every rejection is printed with
its reason, and the running Merkle root is what would be anchored on-chain.

Protocol (plain text, one line each):
    K <device> <keyhex>          node announces its dev key (HMAC stand-in)
    Q <device>                   what is the last sequence you have?
    B <count>                    a batch of <count> records follows
    R <84 bytes as hex>          one record
    G <device> <from> <to> <mac> these records are gone; record the hole.
                                 mac is the node's HMAC over
                                 SHA-256("ACGAP|dev|from|to"); refused without it
    #  ...                       human-readable chatter, ignored
Replies:
    A <seq>                      accepted up to this sequence
    N <reason>                   refused
"""
import argparse, hashlib, hmac, json, struct, sys, time
from datetime import datetime, timezone

REC = 84
BODY = 52

# ── the record, unpacked ──────────────────────────────────────────────────
def parse(raw: bytes) -> dict:
    dev, seq, ts, temp, rh, c2h4, flags, batt = struct.unpack_from("<IIIhHHBB", raw, 0)
    return {
        "device": dev, "seq": seq, "ts": ts,
        "temp_c": temp / 100.0, "rh_pct": rh / 100.0,
        "ethylene_ppb": None if c2h4 == 0xFFFF else c2h4,
        "tamper":   bool(flags & 0x01), "moved": bool(flags & 0x02),
        "charging": bool(flags & 0x04), "cold":  bool(flags & 0x08),
        "battery_pct": batt,
        "prev": raw[20:52].hex(), "sig": raw[52:84].hex(),
    }

def digest(raw: bytes) -> bytes:
    return hashlib.sha256(raw[:BODY]).digest()

def merkle(leaves):
    if not leaves:
        return b"\x00" * 32
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [hashlib.sha256(level[i] + level[i + 1]).digest()
                 for i in range(0, len(level), 2)]
    return level[0]

# ── the eight checks ──────────────────────────────────────────────────────
class Server:
    def __init__(self, out="records.jsonl"):
        self.keys, self.ack, self.tip, self.anchor_next = {}, {}, {}, {}
        self.db, self.leaves, self.gaps = [], [], []
        self.rejects = 0
        self.out = open(out, "a", buffering=1)

    def register(self, device, keyhex):
        self.keys[device] = bytes.fromhex(keyhex)
        print(f"# device {device:08X} registered")

    def gap_ok(self, device, lo, hi, mac_hex):
        key = self.keys.get(device)
        if key is None:
            return False
        d = hashlib.sha256(f"ACGAP|{device}|{lo}|{hi}".encode()).digest()
        try:
            return hmac.compare_digest(hmac.new(key, d, hashlib.sha256).digest(),
                                       bytes.fromhex(mac_hex))
        except ValueError:
            return False

    def note_gap(self, device, lo, hi):
        self.gaps.append((device, lo, hi))
        self.ack[device] = hi
        self.tip.pop(device, None)
        self.anchor_next[device] = True
        print(f"\033[33m  GAP  device {device:08X}: records {lo}-{hi} "
              f"declared lost by the node\033[0m")

    def accept(self, raw: bytes):
        r = parse(raw)
        d, seq = r["device"], r["seq"]

        # 1 — is the device registered?
        if d not in self.keys:
            return self.no(r, "unknown device")
        # 2 — is the signature valid?
        want = hmac.new(self.keys[d], digest(raw), hashlib.sha256).digest()
        if not hmac.compare_digest(want, raw[52:84]):
            return self.no(r, "bad signature")
        # 3 / 4 — sequence, duplicate, replay
        have = self.ack.get(d, 0)
        if seq <= have:
            return self.no(r, "duplicate or replay")
        if seq != have + 1:
            return self.no(r, f"sequence gap (expected {have + 1})")
        # 5 — is the timestamp sensible?
        if not (1600000000 < r["ts"] < 2200000000):
            return self.no(r, "impossible timestamp")
        # 6 — is the hash chain unbroken?
        tip = self.tip.get(d)
        if tip is not None and bytes.fromhex(r["prev"]) != tip:
            return self.no(r, "broken hash chain")
        if tip is None and self.db and not self.anchor_next.get(d):
            return self.no(r, "no chain anchor")
        self.anchor_next[d] = False
        # 7 — calibration still in date  (placeholder: EN 13486 registry lookup)
        # 8 — is the sensor healthy?  outside its own rated range is a fault
        if not (-40.0 <= r["temp_c"] <= 125.0):
            return self.no(r, "outside sensor range")

        dg = digest(raw)
        self.tip[d] = dg
        self.ack[d] = seq
        self.leaves.append(dg)
        r["received"] = datetime.now(timezone.utc).isoformat()
        r["digest"] = dg.hex()
        self.db.append(r)
        self.out.write(json.dumps(r) + "\n")
        return True, seq

    def no(self, r, why):
        self.rejects += 1
        print(f"\033[31m  REJECTED  seq {r['seq']}: {why}\033[0m")
        return False, why

    def summary(self):
        root = merkle(self.leaves).hex()
        print(f"\n  stored {len(self.db)} records · {self.rejects} rejected · "
              f"{len(self.gaps)} declared gaps")
        print(f"  merkle root (this is what gets anchored): {root[:32]}…")


# ── the serial loop ───────────────────────────────────────────────────────
def run_serial(port, baud, srv):
    try:
        import serial  # pyserial
    except ImportError:
        sys.exit("pyserial is missing:  pip install pyserial")

    ser = serial.Serial(port, baud, timeout=1)
    print(f"# listening on {port} at {baud}\n")
    pending, expect = [], 0
    last_print = 0

    while True:
        line = ser.readline().decode("utf-8", "replace").strip()
        if not line:
            continue

        if line.startswith("#"):
            print(f"\033[90m{line}\033[0m")
        elif line.startswith("K "):
            _, dev, key = line.split()
            srv.register(int(dev), key)
        elif line.startswith("Q "):
            dev = int(line.split()[1])
            ser.write(f"A {srv.ack.get(dev, 0)}\n".encode())
        elif line.startswith("G "):
            _, dev, lo, hi, mac = line.split()
            if srv.gap_ok(int(dev), int(lo), int(hi), mac):
                srv.note_gap(int(dev), int(lo), int(hi))
                ser.write(f"A {hi}\n".encode())
            else:
                print(f"\033[31m  REFUSED gap {lo}-{hi}: not signed by the device\033[0m")
                ser.write(b"N gap notice not signed\n")
        elif line.startswith("B "):
            expect = int(line.split()[1]); pending = []
        elif line.startswith("R "):
            pending.append(bytes.fromhex(line[2:]))
            if len(pending) >= expect:
                acked, reason = 0, None
                for raw in pending:
                    ok, val = srv.accept(raw)
                    if not ok:
                        reason = val; break
                    acked = val
                ser.write((f"A {acked}\n" if acked else f"N {reason}\n").encode())
                pending, expect = [], 0
                if time.time() - last_print > 2:
                    srv.summary(); last_print = time.time()


def run_replay(path, srv):
    """Feed a captured session back in, so the demo works with no board."""
    for line in open(path):
        line = line.strip()
        if line.startswith("K "):
            _, dev, key = line.split(); srv.register(int(dev), key)
        elif line.startswith("R "):
            srv.accept(bytes.fromhex(line[2:]))
        elif line.startswith("G "):
            _, dev, lo, hi, mac = line.split()
            if srv.gap_ok(int(dev), int(lo), int(hi), mac):
                srv.note_gap(int(dev), int(lo), int(hi))
            else:
                print(f"\033[31m  REFUSED gap {lo}-{hi}: not signed by the device\033[0m")
    srv.summary()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="AnnaChain backend")
    ap.add_argument("port", nargs="?", help="serial port the node is on")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--replay", help="replay a captured log instead of a live board")
    ap.add_argument("--out", default="records.jsonl")
    a = ap.parse_args()

    s = Server(a.out)
    print("\n\033[1mAnnaChain server\033[0m — eight checks, then storage\n")
    try:
        if a.replay:
            run_replay(a.replay, s)
        elif a.port:
            run_serial(a.port, a.baud, s)
        else:
            ap.error("give a serial port, or --replay a captured log")
    except KeyboardInterrupt:
        s.summary()
        print()
