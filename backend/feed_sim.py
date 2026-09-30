#!/usr/bin/env python3
"""AnnaChain — replay a captured trip into the running server.

    py backend/feed_sim.py demo.capture

Make the capture first (needs g++, which you already have), and replay it
soon after: the trip ends at the moment it is captured, and the server refuses
readings older than a node could have held them (30 days).

    g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac lib/ac/ac_sha256.cpp ^
        lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp ^
        lib/ac/ac_sim.cpp tools/dump.cpp -o dump.exe
    dump.exe 1000 350 > demo.capture

Enrolling a device is an operator action, so this signs in (--user and
--password, the first-run account by default). Records themselves need no
login: each one carries the device's signature.

The point of this script is the pacing. It sends records at a watchable rate,
then goes completely silent for the length of the blind spot — the dashboard
sees nothing at all, exactly as it would on a real truck past Dhule — and then
delivers the backlog in batches. Nobody has to be told what happened; it is
visible.
"""
import argparse, json, struct, sys, time, urllib.request, urllib.error


def post(base, path, payload, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        base + path, data=json.dumps(payload).encode(),
        headers=headers, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def get(base, path, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    req = urllib.request.Request(base + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def first_timestamp(lines):
    """The time of the first record in the capture: the oldest reading in it."""
    for line in lines:
        if line.startswith("R "):
            return struct.unpack_from("<I", bytes.fromhex(line[2:]), 8)[0]
    return None


def check_age(capture, lines, window_s, allow_stale, now=None):
    """Refuse, before anything is posted, a capture the server would refuse.

    The server refuses a reading older than window_s (check 5, MAX_HOLD_S in
    checks.py; read from /api/state, never copied here). Past the window every
    record would be refused and nothing on the dashboard would say why. Past a
    quarter of it, the capture is getting old: say so, and carry on."""
    ts = first_timestamp(lines)
    if ts is None or not window_s:
        return
    now = time.time() if now is None else now
    age = now - ts
    days, limit = age / 86400, window_s / 86400
    again = ("regenerate: mingw32-make fleet\n"
             f"replay:     python backend/feed_sim.py {capture} --reset")
    if age > window_s:
        if allow_stale:
            print(f"warning: --allow-stale: feeding a capture {days:.1f} days old; the "
                  f"server refuses readings older than {limit:.0f} days", file=sys.stderr)
            return
        print(f"\n{capture} is {days:.1f} days old. The server refuses readings older "
              f"than {limit:.0f} days (check 5), so every record in it would be "
              f"refused. Nothing was sent.\n{again}\n"
              f"(--allow-stale feeds it anyway, for an archived capture.)",
              file=sys.stderr)
        sys.exit(2)
    if age > window_s / 4:
        print(f"warning: {capture} is {days:.1f} days old; the server refuses "
              f"readings older than {limit:.0f} days. Feeding it; to start fresh:\n"
              f"{again}", file=sys.stderr)


def sign_in(base, username, password):
    """Enrolling devices and --reset need a login. Ingesting records does not:
    a node proves itself with the signature on every record, not a password."""
    try:
        return post(base, "/api/login",
                    {"username": username, "password": password})["token"]
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit(f"\ncannot sign in as '{username}'.\n"
                     f"Enrolling the capture's devices needs an operator login: "
                     f"pass --user and --password.")
        raise


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", help="file produced by dump.exe")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--rate", type=float, default=12,
                    help="records per second while online (default 12)")
    ap.add_argument("--silence", type=float, default=12,
                    help="real seconds to stay dark, standing in for 29 hours")
    ap.add_argument("--batch", type=int, default=20,
                    help="records per request, matching the node's batch size")
    ap.add_argument("--reset", action="store_true",
                    help="wipe the server first (needs an operator login)")
    ap.add_argument("--allow-stale", action="store_true",
                    help="feed a capture older than the server's window anyway "
                         "(an archived capture); the server will still refuse "
                         "readings it considers too old")
    ap.add_argument("--no-calibration", action="store_true",
                    help="do not record a demo calibration for the capture's devices")
    ap.add_argument("--user", default="operator")
    ap.add_argument("--password", default="annachain")
    a = ap.parse_args(argv)

    lines = [l.strip() for l in open(a.capture, encoding="utf-8-sig") if l.strip()]
    try:
        token = sign_in(a.base, a.user, a.password)
    except urllib.error.URLError as e:
        sys.exit(f"\ncannot reach {a.base} — is uvicorn running?  ({e})")
    # Before anything is posted: would the server take these readings at all?
    window = get(a.base, "/api/state", token=token).get("max_hold_s")
    check_age(a.capture, lines, window, a.allow_stale)
    if a.reset:
        post(a.base, "/api/reset", {}, token=token)
        print("server wiped")

    def enrol(payload):
        try:
            return post(a.base, "/api/register", payload, token=token)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                sys.exit(f"\ndevice {payload['device']:08X} is already enrolled with a "
                         f"different key. Start from a fresh database (make clean), "
                         f"or rotate the key deliberately as an admin.")
            raise

    # Check 7 needs a calibration to check. A demo device has no certificate, so
    # record a plausible one and say that it is a demo: verified 60 days before
    # the trip, on the usual 12-month EN 13486 interval.
    trip_start = first_timestamp(lines) or time.time()
    cal_date = trip_start - 60 * 86400

    def calibrate(dev):
        if a.no_calibration:
            return
        post(a.base, f"/api/calibration/{dev}",
             {"cal_date": cal_date, "months": 12,
              "ref": "DEMO calibration record, no certificate (feed_sim.py)"},
             token=token)
        print(f"  calibration: DEMO record, verified "
              f"{time.strftime('%d %b %Y', time.gmtime(cal_date))}, 12-month interval")

    device, pending, sent, offline, offline_done = None, [], 0, False, False
    keys = {}                     # device -> key hex, until the label line arrives

    def dev_of(hexrec):
        raw = bytes.fromhex(hexrec[:8])
        return int.from_bytes(raw, "little")
    t_start = time.time()

    def flush(catching_up=False):
        # Online, the node transmits each reading as it is taken: one record per
        # request. Only when draining a backlog does it batch. Keeping that
        # faithful is what lets the server tell the two apart.
        nonlocal pending, sent
        size = a.batch if catching_up else 1
        while pending:
            # Never mix devices in one batch: each node has its own chain, and
            # the server checks them independently.
            d0 = dev_of(pending[0])
            n = 0
            while n < len(pending) and n < size and dev_of(pending[n]) == d0:
                n += 1
            chunk, pending = pending[:n], pending[n:]
            try:
                res = post(a.base, "/api/ingest", {"records": chunk})
            except urllib.error.URLError as e:
                sys.exit(f"\ncannot reach {a.base} — is uvicorn running?  ({e})")
            sent += res["accepted"]
            if res["rejected"]:
                print(f"\n  server refused a record: {res['reason']}")
            print(f"\r  sent {sent:5d}   last ack {res['last_ack']:5d}",
                  end="", flush=True)
            time.sleep(len(chunk) / max(a.rate, 1))


    for line in lines:
        if line.startswith("K "):
            _, dev, key = line.split()
            device = int(dev)
            keys[device] = key
            enrol({"device": device, "key_hex": key})
            print(f"registered device {device:08X}")
            calibrate(device)

        elif line.startswith("L "):
            # L <device> <truck> <label...>  — names the node and its truck
            _, dev, truck, label = line.split(maxsplit=3)
            enrol({"device": int(dev), "key_hex": keys[int(dev)],
                   "label": label, "truck": truck})
            print(f"  {label} on {truck}")

        elif line.startswith("R "):
            pending.append(line[2:])
            if len(pending) >= (a.batch if offline_done else 1):
                flush(catching_up=offline_done)

        elif line.startswith("G "):
            # G <device> <from> <to> <mac>: the device signed the notice
            flush(catching_up=offline_done)
            _, dev, lo, hi, mac = line.split()
            try:
                post(a.base, "/api/gap", {"device": int(dev), "from_seq": int(lo),
                                          "to_seq": int(hi), "mac": mac})
            except urllib.error.HTTPError as e:
                sys.exit(f"\nthe server refused the gap notice for {lo}-{hi}: "
                         f"{e.code} {e.read().decode(errors='replace')}")
            print(f"\n  declared lost: records {lo}-{hi}")

        elif line == "# offline":
            flush()
            offline = True
            print(f"\n\n  --- blind spot: going silent for {a.silence:.0f} s "
                  f"(29 hours on the road) ---")
            print("  the node is still sampling and still saving. "
                  "The server sees nothing.\n")
            time.sleep(a.silence)

        elif line == "# online":
            if offline:
                print("  --- signal back: sending only what the server is missing ---")
                offline = False
                offline_done = True

    flush(catching_up=offline_done)
    took = time.time() - t_start
    print(f"\n\ndone: {sent} records in {took:.0f} s")
    print(f"open {a.base} to see the trip")


if __name__ == "__main__":
    main()
