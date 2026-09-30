#!/usr/bin/env python3
"""AnnaChain — the bridge, for the day the board arrives.

    py backend/bridge_serial.py COM5

The node speaks a tiny line protocol over USB. This forwards it to the HTTP API
and passes the server's answers back, so the ESP32 and the dashboard are talking
to each other with nothing in between that invents anything.

When the SX1262 arrives, the gateway does this job over LoRa and this script is
no longer needed. Until then the USB cable is the network, and pressing BOOT on
the board is the antenna being pulled.

    node -> B <count>            a batch of <count> records follows
    node -> R <hex>              one 84-byte record
    node -> Q <device>           what is the last sequence you have?
    node -> G <dev> <from> <to> <mac>  these records are gone (signed by the node)
    node -> K <dev> <keyhex>     development key announcement
    node -> T <dev> <assign|tap> <uid> <time>  a PN532 tap: becomes a checkpoint
    node -> # ...                human-readable chatter, echoed, not parsed
    us   -> A <seq>              accepted up to here
    us   -> A <seq> <unix>       the answer to Q: last-ACK and the server's clock,
                                 which is how the node learns the time. A board
                                 that predates the time field reads <seq> with
                                 toInt(), which stops at the space, so it is
                                 unaffected. An unknown device gets "A 0", no time.
    us   -> N <reason>           refused
"""
import argparse, json, sys, urllib.request, urllib.error


def call(base, path, payload=None, method="POST", token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("port", help="serial port, e.g. COM5 or /dev/ttyACM0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--user", default="operator",
                    help="operator login, needed to enrol the node (not to send records)")
    ap.add_argument("--password", default="annachain")
    a = ap.parse_args(argv)
    token = call(a.base, "/api/login",
                 {"username": a.user, "password": a.password})["token"]

    try:
        import serial
    except ImportError:
        sys.exit("pyserial is missing:  py -m pip install pyserial")

    ser = serial.Serial(a.port, a.baud, timeout=1)
    print(f"bridging {a.port} <-> {a.base}\n")

    pending, expect, device = [], 0, None

    def reply(line):
        ser.write((line + "\n").encode())

    while True:
        try:
            line = ser.readline().decode("utf-8", "replace").strip()
        except serial.SerialException as e:
            sys.exit(f"\nserial port closed: {e}")
        if not line:
            continue

        try:
            if line.startswith("#"):
                print("\033[90m" + line + "\033[0m")

            elif line.startswith("K "):
                _, dev, key = line.split()
                device = int(dev)
                try:
                    r = call(a.base, "/api/register",
                             {"device": device, "key_hex": key, "label": "Crate node"},
                             token=token)
                    print(f"registered {device:08X}, server has {r['last_ack']}")
                except urllib.error.HTTPError as e:
                    # 409: the board presented a different key from the one on
                    # file. That is either a wiped board or an attack, and it is
                    # not this script's job to decide which.
                    print(f"\033[31m  enrolment refused ({e.code}): "
                          f"{e.read().decode(errors='replace')}\033[0m")

            elif line.startswith("Q "):
                dev = int(line.split()[1])
                try:
                    r = call(a.base, f"/api/lastack/{dev}", method="GET")
                    now = r.get("now")          # an older server sends none
                    reply(f"A {r['last_ack']} {now}" if now else f"A {r['last_ack']}")
                except urllib.error.HTTPError:
                    reply("A 0")          # unknown device: start from the beginning

            elif line.startswith("B "):
                expect, pending = int(line.split()[1]), []

            elif line.startswith("R "):
                pending.append(line[2:])
                if len(pending) >= expect:
                    r = call(a.base, "/api/ingest", {"records": pending})
                    if r["accepted"]:
                        print(f"  +{r['accepted']:3d}  ack {r['last_ack']}")
                        reply(f"A {r['last_ack']}")
                    else:
                        print(f"  \033[31mrefused: {r['reason']}\033[0m")
                        reply(f"N {r['reason']}")
                    pending, expect = [], 0

            elif line.startswith("T "):
                # T <device> <assign|tap> <uid-hex> <unix-time>: a PN532 tap
                # (src/main.cpp, env:node_lora). It becomes a checkpoint on the
                # node's shipment. It is a logged claim: not signed by the
                # device and not in the hash chain, and the note says so.
                # This is the only way a tap reaches the server — over LoRa it
                # is not carried (no FRAME_TAP; backend/README.md).
                _, dev, what, uid, ts = line.split()
                step = "commissioning" if what == "assign" else "inspecting"
                try:
                    call(a.base, "/api/checkpoint",
                         {"shipment_id": f"AC-{int(dev):08X}", "place": f"NFC tag {uid}",
                          "biz_step": step, "ts": int(ts),
                          "note": "PN532 tap over USB: not signed, not in the hash chain"},
                         token=token)
                    print(f"  tap {what} {uid} -> {step}")
                except urllib.error.HTTPError as e:
                    print(f"\033[31m  tap {uid} not recorded ({e.code}): "
                          f"{e.read().decode(errors='replace')}\033[0m")

            elif line.startswith("G "):
                _, dev, lo, hi, mac = line.split()
                try:
                    call(a.base, "/api/gap", {"device": int(dev), "from_seq": int(lo),
                                              "to_seq": int(hi), "mac": mac})
                    print(f"  gap declared: {lo}-{hi}")
                    reply(f"A {hi}")
                except urllib.error.HTTPError as e:
                    print(f"\033[31m  gap refused ({e.code})\033[0m")
                    reply(f"N gap refused {e.code}")

        except urllib.error.URLError as e:
            # The server being down must not knock the node over: stay silent and
            # let it keep buffering, which is exactly what it is designed for.
            print(f"\033[33m  server unreachable ({e.reason}) — node will hold\033[0m")


if __name__ == "__main__":
    main()
