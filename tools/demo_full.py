#!/usr/bin/env python3
"""AnnaChain — the whole server demo, from nothing to a dashboard with a trip in it.

    mingw32-make demo-full          clean, build, capture, serve, feed   (needs g++)
    mingw32-make demo-seed          the fallback: no compiler, a committed capture

Directly:

    python tools/demo_full.py fleet.capture          serve, then feed this capture
    python tools/demo_full.py --seed                 serve, then feed the seed capture

The Makefile targets run `clean` first, so the database starts empty; this
script refuses to start if something is already listening on the port, because
feeding a server that was left running from an earlier session shows the judges
whatever that session did to it.

--seed is for the venue laptop when live generation misbehaves. The committed
capture (tools/seed/fleet.seed.capture: three nodes, one drifting, made with
`fleet 300 120`) was true on the day it was made, and would be refused 30 days
later by check 5 like any old capture. So it is RE-TIMED at seed time: every
timestamp is shifted so the trip ends now, and every record is re-chained and
re-signed with the capture's own dev keys (the K lines). That is only possible
because those keys are published development keys; it is exactly what
`fleet 300 120 --start <new start>` would print, and
backend/tests/test_feed_sim.py checks that byte for byte. It could not be done
to a real device's records, and should not be. The fed database is then
known-good: 3 shipments, 420 records each, node B suspect.

The server keeps running after the feed, until Ctrl+C. --exit-after-feed stops
it instead (for checking the pipeline end to end).
"""
import argparse, hashlib, hmac, os, socket, struct, subprocess, sys, time
import urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "tools" / "seed" / "fleet.seed.capture"
STEP = 300                                   # the nodes sample every 5 minutes


# ── re-timing a capture made with published dev keys ──────────────────────
def retime(lines, start=None, now=None):
    """Shift a capture so its trip starts at `start` (default: ends now, as a
    fresh `fleet` run would), re-chaining and re-signing every record with the
    capture's own keys. Returns the new lines."""
    keys, recs = {}, []
    for l in lines:
        if l.startswith("K "):
            _, dev, key = l.split()
            keys[int(dev)] = bytes.fromhex(key)
        elif l.startswith("R "):
            recs.append(bytes.fromhex(l[2:].strip()))
    if not recs:
        return list(lines)
    ts = [struct.unpack_from("<I", r, 8)[0] for r in recs]
    first, last = min(ts), max(ts)
    if start is None:                        # tools/fleet.cpp tripStart(), same rule
        now = int(time.time()) if now is None else int(now)
        n = (last - first) // STEP + 1
        start = now - n * STEP
        start -= start % STEP
    delta = int(start) - first

    tip = {}                                 # (device, seq) -> new digest
    out = []
    for l in lines:
        if not l.startswith("R "):
            out.append(l)
            continue
        raw = bytearray(bytes.fromhex(l[2:].strip()))
        dev, seq, t = struct.unpack_from("<III", raw, 0)
        if dev not in keys:
            raise ValueError(f"record for device {dev} with no K line: cannot re-sign")
        struct.pack_into("<I", raw, 8, t + delta)
        if any(raw[20:52]):                  # not the first record of its chain
            prev = tip.get((dev, seq - 1))
            if prev is None:
                raise ValueError(f"device {dev} record {seq}: the record before it is "
                                 f"not in the capture, so it cannot be re-chained")
            raw[20:52] = prev
        d = hashlib.sha256(bytes(raw[:52])).digest()
        raw[52:84] = hmac.new(keys[dev], d, hashlib.sha256).digest()
        tip[(dev, seq)] = d
        out.append("R " + bytes(raw).hex())
    return out


# ── serving ───────────────────────────────────────────────────────────────
def port_in_use(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def wait_until_up(base, proc, seconds=30):
    t0 = time.time()
    while time.time() - t0 < seconds:
        if proc.poll() is not None:
            return False
        try:
            urllib.request.urlopen(base + "/api/lastack/0", timeout=1)
        except urllib.error.HTTPError as e:
            if e.code == 404:                # up, and it does not know device 0
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.3)
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("capture", nargs="?", help="capture to feed (default with --seed: the seed)")
    ap.add_argument("--seed", action="store_true",
                    help="feed the committed seed capture, re-timed to end now")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--rate", type=float, help="records per second (feed_sim default 12)")
    ap.add_argument("--silence", type=float, help="seconds of blind spot (feed_sim default 12)")
    ap.add_argument("--exit-after-feed", action="store_true",
                    help="stop the server when the feed is done")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)   # interleave with the children
    except AttributeError:
        pass

    if a.seed:
        src = Path(a.capture) if a.capture else SEED
        lines = src.read_text(encoding="utf-8-sig").splitlines()
        capture = ROOT / "seed.capture"
        capture.write_text("\n".join(retime(lines)) + "\n", encoding="utf-8")
        print(f"seed: {src.name} re-timed to end now and re-signed with its dev keys "
              f"-> {capture.name}")
        # A seed is a known-good database, fast: full speed, but a silence longer
        # than the server's SILENCE_S so the outage is still seen as one.
        rate = a.rate or 1000
        silence = a.silence if a.silence is not None else 4
    else:
        if not a.capture:
            ap.error("give a capture file, or --seed")
        capture = Path(a.capture)
        rate, silence = a.rate, a.silence

    base = f"http://127.0.0.1:{a.port}"
    if port_in_use(a.port):
        sys.exit(f"something is already listening on port {a.port}. Stop the old "
                 f"server first: feeding it would show whatever it already holds.")

    print(f"serving on {base}  (and on this laptop's IP, port {a.port})")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app:app", "--host", "0.0.0.0",
         "--port", str(a.port), "--log-level", "warning"], cwd=ROOT)
    try:
        if not wait_until_up(base, server):
            sys.exit("the server did not start (is uvicorn installed? "
                     "python -m pip install -r backend/requirements.txt)")
        feed = [sys.executable, str(ROOT / "backend" / "feed_sim.py"), str(capture),
                "--base", base, "--reset"]
        if rate is not None:
            feed += ["--rate", str(rate)]
        if silence is not None:
            feed += ["--silence", str(silence)]
        rc = subprocess.call(feed, cwd=ROOT)
        if rc:
            sys.exit(f"feeding {capture} failed (exit {rc}); the server is stopped")
        print(f"\nopen {base} and sign in (operator / annachain on a fresh database)")
        if a.exit_after_feed:
            return 0
        print("the server keeps running; Ctrl+C to stop it")
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        if server.poll() is None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
