"""AnnaChain — the server.

    py -m pip install fastapi uvicorn
    py -m uvicorn backend.app:app --reload --port 8000
    open http://127.0.0.1:8000

Two things worth knowing about how this is put together.

First, `trip time` and `wall time` are different clocks. A record carries the
moment it was *taken*; it arrives whenever the network allows. The difference
between the two is `lag_s`, and it is the whole story: 0 while the truck has
signal, 29 hours for the records that waited out the blind spot. The dashboard
draws by trip time and colours by lag.

Second, the node is the authority on what it holds and the server is the
authority on what it has received. Neither guesses. `/api/lastack` is how the
node asks.
"""
import io
import math
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth, checks, db, ledger, shelflife
from .alerts import AlertEngine, fmt_dur

STATIC = Path(__file__).with_name("static")

verifier = checks.Verifier()
engine = AlertEngine(db)


@asynccontextmanager
async def lifespan(app):
    """Run startup logic, yield to serve requests, then clean up on shutdown.
    Replaces the deprecated @app.on_event(\"startup\") pattern."""
    db.init()
    for d in db.devices():
        verifier.load(d["device_id"], d["last_ack"], d["tip_digest"], d["anchor_next"],
                      d["last_ts"])
    throttle.load()
    if db.user_count() == 0:
        # First run: one operator account, so the dashboard is never open by
        # accident. The password is printed once and only once.
        salt, pw = auth.hash_password("annachain")
        db.create_user("operator", salt, pw, "admin")
        print("\n  no users existed, so one was created:"
              "\n    username: operator"
              "\n    password: annachain"
              "\n  change it with POST /api/password before anyone else can reach this.\n")
    yield
    # Shutdown: persist the throttle state so a restart does not clear lockouts.
    throttle.save()


app = FastAPI(title="AnnaChain", version="0.3", lifespan=lifespan)

# A node that is online transmits each reading as it is taken, so a batch of
# one is normal traffic and a batch of many is a backlog being drained. That,
# plus a gap in arrival times, is how the server knows a record was held on the
# device rather than sent when it was measured. It needs nothing from the node
# to work this out, and it works the same on a real truck as in a replay.
SILENCE_S = 3.0              # in the field: about 2.5x the sampling interval

last_ingest_at = {}          # device -> wall clock of the last accepted batch
last_batch_n = {}            # device -> size of that batch
silence_start = {}           # device -> when the current silence began
recovering = {}              # device -> how long the silence was, while draining


CAL_DEFAULT_MONTHS = 12          # EN 13486 verification interval
chain = ledger.get_ledger()      # Fabric if it is really there, else local




# ── who is asking ────────────────────────────────────────────────────────
def operator(authorization: str = Header(default="")):
    """Guards everything commercial. The buyer's trace page and the node's own
    ingest path deliberately do not use this."""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    row = db.get_session(token) if token else None
    if not row:
        raise HTTPException(401, "sign in required")
    return row["username"]


class Login(BaseModel):
    username: str
    password: str


class NewPassword(BaseModel):
    old: str
    new: str


throttle = auth.Throttle(db=db)


def _refused(status, detail, wait):
    """The delay goes in the body and in Retry-After, so a client (or a person)
    knows how long to wait instead of guessing."""
    wait = int(math.ceil(wait))
    headers = {"Retry-After": str(wait)} if wait else {}
    return JSONResponse({"detail": detail, "retry_after_s": wait}, status, headers=headers)


@app.post("/api/login")
def login(body: Login, request: Request):
    source = request.client.host if request.client else "?"
    wait = throttle.retry_after(body.username, source)
    if wait > 0:
        return _refused(429, f"too many failed sign-ins; try again in {int(math.ceil(wait))} s",
                        wait)
    u = db.user(body.username)
    # Same answer either way, so this cannot be used to enumerate usernames.
    if not u or not auth.check_password(body.password, u["salt"], u["pwhash"]):
        wait = throttle.failure(body.username, source)
        if wait > 0:
            return _refused(429, f"wrong username or password; too many failed "
                                 f"sign-ins, try again in {int(math.ceil(wait))} s", wait)
        return _refused(401, "wrong username or password", 0)
    throttle.success(body.username, source)
    token = auth.new_token()
    db.put_session(token, u["username"], auth.expiry())
    return {"token": token, "username": u["username"], "role": u["role"]}


@app.post("/api/logout")
def logout(authorization: str = Header(default="")):
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    if token:
        db.drop_session(token)
    return {"ok": True}


@app.get("/api/me")
def me(who: str = Depends(operator)):
    u = db.user(who)
    return {"username": who, "role": u["role"]}


@app.post("/api/password")
def change_password(body: NewPassword, who: str = Depends(operator)):
    u = db.user(who)
    if not auth.check_password(body.old, u["salt"], u["pwhash"]):
        raise HTTPException(401, "current password is wrong")
    if len(body.new) < 8:
        raise HTTPException(400, "use at least 8 characters")
    salt, pw = auth.hash_password(body.new)
    db.create_user(who, salt, pw, u["role"])
    return {"ok": True}


# ── models ───────────────────────────────────────────────────────────────
class Ingest(BaseModel):
    records: list[str]          # each an 84-byte record, hex


class GapNotice(BaseModel):
    device: int
    from_seq: int
    to_seq: int
    mac: str | None = None        # the device's signature over the notice


class Registration(BaseModel):
    device: int
    key_hex: str
    label: str | None = None
    truck: str | None = None      # nodes sharing a truck are compared
    rotate: bool = False          # replace an existing key: admin only, audited


# ── enrolment: an operator action ────────────────────────────────────────
@app.post("/api/register")
def register(reg: Registration, who: str = Depends(operator)):
    """Enrol a device, or update its label and truck.

    A key is set once. Presenting a different key for an enrolled device is
    refused (409) unless it is an explicit rotation by an admin, which is
    written to the audit table. A silent key swap is indistinguishable from an
    attack: it lets whoever did it sign records, and locks the real node out.

    In the field a device is enrolled once, out of band, and the ATECC608B's
    public key is what gets registered, not a shared key."""
    try:
        key = bytes.fromhex(reg.key_hex)
    except ValueError:
        raise HTTPException(400, "key_hex is not hex")
    if len(key) < 16:
        raise HTTPException(400, "key is too short")
    key_hex = key.hex()

    d = db.device(reg.device)
    if not d:
        db.enrol_device(reg.device, key_hex, who, reg.label, reg.truck)
    else:
        if d["key_hex"].lower() != key_hex:
            if not reg.rotate:
                raise HTTPException(
                    409, "this device is already enrolled with a different key; "
                         "replacing it needs rotate=true and an admin")
            if db.user(who)["role"] != "admin":
                raise HTTPException(403, "only an admin can rotate a device key")
            db.rotate_key(reg.device, key_hex, who)
        db.update_device_info(reg.device, reg.label, reg.truck)

    d = db.device(reg.device)
    verifier.load(reg.device, d["last_ack"], d["tip_digest"], d["anchor_next"],
                  d["last_ts"])
    if not db.shipment_for(reg.device):
        db.upsert_shipment(f"AC-{reg.device:08X}", reg.device)
    return {"ok": True, "device": reg.device, "last_ack": d["last_ack"]}


# ── the node's calls ─────────────────────────────────────────────────────


@app.get("/api/lastack/{device}")
def last_ack(device: int):
    """What the server already holds. The node sends only what comes after."""
    d = db.device(device)
    if not d:
        raise HTTPException(404, "unknown device")
    return {"device": device, "last_ack": d["last_ack"]}


@app.post("/api/ingest")
def ingest(body: Ingest):
    """A batch of records. All eight checks, then storage — in that order."""
    now = time.time()
    accepted, rejected, first_reason = 0, 0, None
    dev = None
    ships = {}                 # device -> its shipment row, looked up once
    touched = {}               # device -> newest timestamp accepted in this batch

    # Look at the first record only to find out whose stream this is, so the
    # silence can be measured before anything is stored.
    try:
        dev0 = checks.parse(bytes.fromhex(body.records[0]))["device"]
    except Exception:
        dev0 = None
    quiet = now - last_ingest_at[dev0] if dev0 in last_ingest_at else 0.0
    after_silence = quiet > SILENCE_S

    if after_silence:
        recovering[dev0] = quiet
    elif len(body.records) == 1 and dev0 in recovering:
        recovering.pop(dev0)          # back to one-at-a-time: the backlog is drained

    # Every record in the backlog was held, not just the first batch of it.
    held = dev0 in recovering
    lag = max(1, int(recovering.get(dev0, 0))) if held else 0

    for hexrec in body.records:
        try:
            raw = bytes.fromhex(hexrec)
        except ValueError:
            rejected += 1
            first_reason = first_reason or "not a record"
            continue
        if checks.record_format(raw) != checks.FORMAT_V1:
            rejected += 1
            first_reason = first_reason or checks.format_refusal(raw)
            continue

        r = checks.parse(raw)
        dev = r["device"]
        d = db.device(dev)
        key = bytes.fromhex(d["key_hex"]) if d else None

        seen = bool(db.conn().execute(
            "SELECT 1 FROM records WHERE device_id=? AND seq=?", (dev, r["seq"])
        ).fetchone())

        cal_due = None
        if d and d["cal_date"]:
            cal_due = d["cal_date"] + (d["cal_months"] or CAL_DEFAULT_MONTHS) * 2629800
        ok, parsed, digest, reason = verifier.check(raw, key, seen, cal_due, now=now)
        if not ok:
            rejected += 1
            first_reason = first_reason or reason
            db.add_reject(dev, r["seq"], reason)
            break        # the chain cannot continue past a bad record

        if dev not in ships:
            ships[dev] = db.shipment_for(dev)
        ship = ships[dev]

        parsed["digest"] = digest.hex()
        parsed["raw"] = raw                    # the evidence; everything else is an index
        parsed["received"] = now
        parsed["lag_s"] = lag
        parsed["stale_calibration"] = dev in verifier.stale_calibration
        db.insert_record(parsed)
        engine.on_record(parsed, ship)
        if parsed["stale_calibration"]:
            engine.on_stale_calibration(dev, d["cal_date"], cal_due)
        touched[dev] = max(touched.get(dev, 0), parsed["ts"])
        accepted += 1

    if accepted:
        # A batch can carry records from more than one node, so every device it
        # touched needs its own tip written — not just the last one seen.
        for d in touched:
            tip = verifier.tip.get(d)
            db.set_tip(d, verifier.ack[d], tip.hex() if tip else None,
                       int(verifier.anchor_next.get(d, False)),
                       verifier.last_ts.get(d))
        db.commit()

        for d, ts in touched.items():
            _diagnose(d, ts)
        db.commit()

        if after_silence:
            engine.on_recovery(dev, accepted, quiet)
            db.commit()
        silence_start.pop(dev, None)
        last_ingest_at[dev] = now
        last_batch_n[dev] = accepted

    db.log_ingest(len(body.records), accepted)
    return {"accepted": accepted, "rejected": rejected,
            "reason": first_reason,
            "last_ack": verifier.ack.get(dev, 0) if dev else 0}


@app.post("/api/gap")
def gap(notice: GapNotice):
    """The node is telling us records no longer exist. Record it and re-anchor.

    This is a claim about the record, so it needs the same standing as a record:
    the device's own signature over gapDigest(device, from, to). An unsigned
    notice would let anyone who can reach this server punch a hole in a
    consignment. And it must pick up exactly where the server is, or an old
    notice replayed later would wind the chain back."""
    d = db.device(notice.device)
    if not d:
        raise HTTPException(404, "unknown device")
    key = checks._key_at(db.keys_for(notice.device), notice.from_seq)
    if not checks.gap_mac_ok(key, notice.device, notice.from_seq, notice.to_seq,
                             notice.mac):
        db.add_reject(notice.device, notice.from_seq, "gap notice not signed by the device")
        raise HTTPException(401, "gap notice is not signed by this device")
    if notice.to_seq < notice.from_seq or notice.from_seq != (d["last_ack"] or 0) + 1:
        raise HTTPException(
            409, f"a gap has to start at the next record the server expects "
                 f"({(d['last_ack'] or 0) + 1})")
    db.add_gap(notice.device, notice.from_seq, notice.to_seq, notice.mac)
    verifier.note_gap(notice.device, notice.to_seq)
    db.set_tip(notice.device, notice.to_seq, None, 1)
    engine.on_gap(notice.device, notice.from_seq, notice.to_seq)
    db.commit()
    return {"ok": True, "last_ack": notice.to_seq}


# ── what the dashboard reads ─────────────────────────────────────────────
BUCKET_S = 300          # nodes sample every 5 minutes; compare within one slot
_diagnosed = set()      # (truck, bucket) already looked at


def _diagnose(device, ts):
    """Compare this node against the others on its truck, for this time slot."""
    d = db.device(device)
    if not d or not d["truck"]:
        return
    truck = d["truck"]
    bucket = (ts // BUCKET_S) * BUCKET_S
    if (truck, bucket) in _diagnosed:
        return

    rows = db.readings_in_bucket(truck, bucket, bucket + BUCKET_S)
    readings = {r["device_id"]: r["temp_c"] for r in rows}
    if len(readings) < len(db.devices_on_truck(truck)):
        return                      # not everyone has reported yet; wait
    _diagnosed.add((truck, bucket))
    engine.diagnose(truck, bucket, readings)


@app.get("/api/truck/{truck}")
def truck_view(truck: str, who: str = Depends(operator)):
    """Every node on one truck, side by side. Seeing one line drift away from
    the others is more convincing than being told that it did."""
    nodes = db.devices_on_truck(truck)
    if not nodes:
        raise HTTPException(404, "no such truck")
    out = []
    for n in nodes:
        rows = db.records_window(n["device_id"], 1200)
        out.append({
            "device": n["device_id"],
            "label": n["label"],
            "suspect": n["device_id"] in engine.suspect,
            "latest": rows[-1]["temp_c"] if rows else None,
            "series": [{"ts": r["ts"],
                        "t": None if (r["flags"] & checks.FLAG_SENSORBAD)
                             else r["temp_c"]} for r in rows],
        })
    temps = sorted(x["latest"] for x in out if x["latest"] is not None)
    median = temps[len(temps) // 2] if temps else None
    return {"truck": truck, "median": median, "nodes": out,
            "suspects": sorted(engine.suspect)}


@app.get("/api/state")
def state(device: int | None = None, who: str = Depends(operator)):
    devs = db.devices()
    if not devs:
        return {"devices": [], "waiting": True}

    if device is None:
        device = devs[0]["device_id"]
    d = db.device(device)
    if not d:
        raise HTTPException(404, "unknown device")

    now = time.time()
    seen = last_ingest_at.get(device)
    quiet_s = (now - seen) if seen else None

    if quiet_s is None:
        status, detail = "waiting", "no records yet"
    elif quiet_s > SILENCE_S:
        if device not in silence_start:
            silence_start[device] = seen
        status = "silent"
        detail = f"nothing received for {fmt_dur(quiet_s)}"
    elif last_batch_n.get(device, 1) > 1:
        status = "catching up"
        detail = f"{last_batch_n[device]} held records just arrived"
    else:
        status, detail = "live", "records arriving normally"

    rows = db.records_window(device, 1500)
    ship = db.shipment_for(device)
    gaps = db.gaps(device)
    lost = sum(g["to_seq"] - g["from_seq"] + 1 for g in gaps)

    series = [{
        "seq": r["seq"], "ts": r["ts"],
        "t": None if (r["flags"] & checks.FLAG_SENSORBAD) else r["temp_c"],  # a fault is a hole, not a zero
        "h": None if (r["flags"] & checks.FLAG_SENSORBAD) else r["rh_pct"],
        "e": r["c2h4_ppb"], "b": r["batt_pct"],
        "lag": r["lag_s"], "f": r["flags"],
    } for r in rows]

    recovered = db.conn().execute(
        "SELECT COUNT(*) n FROM records WHERE device_id=? AND lag_s>0", (device,)
    ).fetchone()["n"]

    return {
        "device": device,
        "label": d["label"],
        "truck": d["truck"],
        "suspect": device in engine.suspect,
        "status": status,
        "detail": detail,
        "quiet_s": quiet_s,
        "shipment": dict(ship) if ship else None,
        "stored": db.record_count(device),
        "last_seq": d["last_ack"],
        "recovered": recovered,
        "declared_lost": lost,
        "series": series,
        "alerts": [dict(a) for a in db.alerts(30, device_id=device)],
        "rejects": [dict(x) for x in db.rejects(10)],
        "truck_alerts": [dict(a) for a in db.alerts(12)],
        "anchors": [dict(a) for a in db.anchors(device, 5)],
        "devices": [{"id": x["device_id"], "label": x["label"]} for x in devs],
    }


@app.get("/api/verify/{device}")
def verify(device: int, full: bool = False):
    """Re-derive the chain from the stored records.

    Walking every record is fine at a few thousand and wrong at ninety days'
    worth. So verification resumes from the last *mark* — a sequence number
    whose digest and Merkle root were computed and stored earlier — and only
    re-checks what has arrived since. `?full=true` forces the whole walk, which
    is what an auditor who does not trust our marks would ask for, and is the
    only honest way to offer the shortcut at all.

    Every record walked is re-derived from the 84 bytes the device sent: its
    digest recomputed, its signature re-checked against the device key, its
    link to the record before compared against the *recomputed* digest of that
    record, and every stored column compared against a fresh parse. See
    checks.verify_chain. The stored digest/prev/value columns are never trusted.

    A mark is only an operator convenience. Anything shown to a buyer, and any
    anchor, uses full=True.
    """
    rows = db.records_for_verify(device)
    gaps = db.gaps(device)
    declared = [{"from": g["from_seq"], "to": g["to_seq"]}
                for g in sorted(gaps, key=lambda g: g["from_seq"])]
    if not rows:
        return {"device": device, "records": 0, "checked_now": 0, "ok": True,
                "broken_at": None, "reason": None, "root": None,
                "declared_gaps": declared}
    keys = db.keys_for(device)

    mark, start, start_i = (None if full else db.last_mark(device)), None, 0
    if mark:
        for i, r in enumerate(rows):
            if r["seq"] == mark["seq"]:
                # The marked record must still be the one that was verified.
                if r["raw"] is None or checks.digest(bytes(r["raw"])).hex() != mark["digest"]:
                    mark = None
                else:
                    start, start_i = (r["seq"], bytes.fromhex(mark["digest"])), i + 1
                break
        else:
            mark = None
        if mark is None:
            start, start_i = None, 0

    res = checks.verify_chain(rows[start_i:], keys, gaps, start)

    # The root is over digests recomputed from raw where this walk checked them;
    # before a resumed mark it uses the stored digests the mark vouched for.
    leaves = [bytes.fromhex(r["digest"]) for r in rows[:start_i]] + res["digests"]
    if res["ok"]:
        root = checks.merkle_root(leaves).hex()
        db.put_mark(device, rows[-1]["seq"], res["last"][1].hex() if res["digests"]
                    else rows[-1]["digest"], root)
    else:
        root = None

    return {
        "device": device,
        "records": len(rows),
        "checked_now": res["checked"] + (0 if res["ok"] else 1),
        "resumed_from": mark["seq"] if mark else None,
        "from_seq": rows[0]["seq"],
        "to_seq": rows[-1]["seq"],
        "ok": res["ok"],
        "broken_at": res["broken_at"],
        "reason": res["reason"],
        "detail": res["detail"],
        "columns": res["columns"],
        "root": root,
        "declared_gaps": declared,
        "checked": ("signature, digest, chain link and every stored column, "
                    "re-derived from the device's own bytes"),
    }


@app.post("/api/anchor/{device}")
def anchor(device: int, who: str = Depends(operator)):
    """Put the Merkle root on a ledger.

    Uses Hyperledger Fabric when a network is configured and the local
    hash-linked ledger when it is not — and the response says which, every
    time. See backend/ledger.py for why that matters.
    """
    v = verify(device, full=True)
    if not v["records"]:
        raise HTTPException(400, "nothing to anchor")
    if not v["ok"]:
        raise HTTPException(409, f"chain is broken at record {v['broken_at']}")

    res = chain.append({
        "device": device,
        "from_seq": v["from_seq"],
        "to_seq": v["to_seq"],
        "merkle_root": v["root"],
        "records": v["records"],
        "anchored_by": who,
    })
    db.add_anchor(device, v["from_seq"], v["to_seq"], v["root"])
    db.conn().execute("UPDATE anchors SET chain_txid=? WHERE id=("
                      "SELECT MAX(id) FROM anchors WHERE device_id=?)",
                      (res["txid"], device))
    db.conn().commit()
    return {"ok": True, "root": v["root"], "from_seq": v["from_seq"],
            "to_seq": v["to_seq"], **res,
            "note": ("written to a distributed ledger" if res["distributed"]
                     else "written to the local hash-linked ledger; this is "
                          "tamper-evident but not distributed, and we host it")}


@app.get("/api/ledger")
def ledger_status(who: str = Depends(operator)):
    return {**chain.verify(),
            "distributed": chain.distributed,
            "fabric_configured": ledger.FabricLedger().available()}


# ── checkpoints, and the EPCIS events they become ────────────────────────
class Checkpoint(BaseModel):
    shipment_id: str
    place: str
    biz_step: str = "inspecting"
    disposition: str | None = None
    party: str | None = None
    ts: int | None = None
    note: str | None = None


@app.post("/api/checkpoint")
def add_checkpoint(cp: Checkpoint, who: str = Depends(operator)):
    """One of these per NFC tap on the journey. Until the PN532 is fitted the
    dashboard posts them by hand, which is the same event either way."""
    ship = db.shipment(cp.shipment_id)
    if not ship:
        raise HTTPException(404, "no such shipment")
    ts = cp.ts or int(time.time())
    near = db.conn().execute(
        "SELECT seq FROM records WHERE device_id=? ORDER BY ABS(ts-?) LIMIT 1",
        (ship["device_id"], ts)).fetchone()
    cid = db.add_checkpoint(cp.shipment_id, ship["device_id"], ts, cp.place,
                            cp.biz_step, cp.disposition, cp.party,
                            near["seq"] if near else None, cp.note)
    return {"ok": True, "id": cid}


@app.get("/api/epcis/{shipment_id}")
def epcis(shipment_id: str):
    """The shipment as GS1 EPCIS 2.0 events — one per real checkpoint, each
    carrying the sensor readings for the leg that ended there. This is the form
    FSSAI and APEDA systems can read without a translator."""
    ship = db.shipment(shipment_id)
    if not ship:
        raise HTTPException(404, "no such shipment")
    device = ship["device_id"]
    rows = db.records_window(device, 20000)
    if not rows:
        raise HTTPException(404, "no records")

    def iso(ts):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))

    epc = f"urn:annachain:shipment:{shipment_id}"
    marks = list(db.checkpoints(shipment_id))
    events = []

    # A journey always has two real ends, whether or not anyone scanned a tag
    # at them. Commissioning at the first reading and receiving at the last are
    # facts the records themselves establish, so they are always emitted —
    # recorded checkpoints go in between, in time order.
    marks = [dict(m) for m in marks]
    if not any(m["biz_step"] == "commissioning" for m in marks):
        marks.append({"ts": rows[0]["ts"], "place": ship["origin"],
                      "biz_step": "commissioning", "disposition": "active",
                      "party": None, "note": None})
    if not any(m["biz_step"] == "receiving" for m in marks):
        marks.append({"ts": rows[-1]["ts"], "place": ship["destination"],
                      "biz_step": "receiving", "disposition": "in_progress",
                      "party": None, "note": None})
    # A checkpoint scanned after the last reading (a dock tap once the node is
    # off) is clamped to the record window, so its leg is not empty.
    for m in marks:
        m["ts"] = max(rows[0]["ts"], min(int(m["ts"]), rows[-1]["ts"]))
    marks.sort(key=lambda m: (m["ts"], m["biz_step"] != "commissioning"))

    prev_ts = rows[0]["ts"]
    for m in marks:
        leg = [r for r in rows
               if prev_ts <= r["ts"] <= m["ts"]
               and not (r["flags"] & checks.FLAG_SENSORBAD)]
        temps = [r["temp_c"] for r in leg]
        ev = {
            "type": "ObjectEvent",
            "action": "OBSERVE" if m["biz_step"] != "commissioning" else "ADD",
            "bizStep": f"urn:epcglobal:cbv:bizstep:{m['biz_step']}",
            "eventTime": iso(m["ts"]),
            "eventTimeZoneOffset": "+05:30",
            "epcList": [epc],
            "readPoint": {"id": f"urn:annachain:place:{m['place']}"},
        }
        if m["disposition"]:
            ev["disposition"] = f"urn:epcglobal:cbv:disp:{m['disposition']}"
        if m["party"]:
            ev["bizTransactionList"] = [{"type": "urn:epcglobal:cbv:btt:po",
                                         "bizTransaction": m["party"]}]
        if temps:
            ev["sensorElementList"] = [{
                "sensorMetadata": {
                    "deviceID": f"urn:annachain:device:{device:08X}",
                    "startTime": iso(prev_ts), "endTime": iso(m["ts"]),
                },
                "sensorReport": [
                    {"type": "gs1:Temperature", "uom": "CEL",
                     "minValue": round(min(temps), 2),
                     "maxValue": round(max(temps), 2),
                     "meanValue": round(sum(temps) / len(temps), 2)},
                ],
            }]
        events.append(ev)
        prev_ts = m["ts"]

    for g in db.gaps(device):
        events.append({
            "type": "ObjectEvent", "action": "OBSERVE",
            "bizStep": "urn:epcglobal:cbv:bizstep:sensor_reporting",
            "eventTime": iso(rows[-1]["ts"]),
            "epcList": [epc],
            "errorDeclaration": {
                "declarationTime": iso(time.time()),
                "reason": "urn:annachain:reason:records_lost_in_device",
                "annachain:from_seq": g["from_seq"],
                "annachain:to_seq": g["to_seq"],
            },
        })

    return {"@context": ["https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld",
                         {"annachain": "urn:annachain:"}],
            "type": "EPCISDocument", "schemaVersion": "2.0",
            "creationDate": iso(time.time()),
            "epcisBody": {"eventList": events}}


# ── the fleet ────────────────────────────────────────────────────────────
@app.get("/api/shipments")
def shipments(who: str = Depends(operator)):
    """Every consignment, not just the first one."""
    out = []
    for s in db.shipments():
        rows = db.conn().execute(
            "SELECT COUNT(*) n, MAX(ts) last, MIN(temp_c) lo, MAX(temp_c) hi "
            "FROM records WHERE device_id=? AND (flags & 32)=0", (s["device_id"],)
        ).fetchone()
        open_alerts = db.conn().execute(
            "SELECT COUNT(*) n FROM alerts WHERE device_id=? AND acked=0 "
            "AND severity!='info'", (s["device_id"],)).fetchone()["n"]
        out.append({
            "shipment_id": s["shipment_id"], "device": s["device_id"],
            "label": s["label"], "truck": s["truck"],
            "commodity": s["commodity"], "origin": s["origin"],
            "destination": s["destination"], "status": s["status"],
            "records": rows["n"], "last_ts": rows["last"],
            "min_c": rows["lo"], "max_c": rows["hi"],
            "alerts": open_alerts,
            "suspect": s["device_id"] in engine.suspect,
        })
    return {"shipments": out}


@app.get("/api/shelflife/{shipment_id}")
def shelf_life(shipment_id: str):
    """How much life is left, and what happened to the rest of it."""
    ship = db.shipment(shipment_id)
    if not ship:
        raise HTTPException(404, "no such shipment")
    rows = db.records_window(ship["device_id"], 20000)
    series = [{"ts": r["ts"],
               "t": None if (r["flags"] & checks.FLAG_SENSORBAD) else r["temp_c"]}
              for r in rows]
    est = shelflife.shelf_life(series, ship["commodity"],
                               ship["min_c"], ship["max_c"])
    est["diagnosis"] = shelflife.classify(
        series, ship["commodity"],
        peer_disagreement=ship["device_id"] in engine.suspect,
        agreed_hi=ship["max_c"])
    return est


# ── proof at the fork ────────────────────────────────────────────────────
# Everything below is public. No key, no login: a buyer holding the carton can
# check the record without asking us for permission, which is the only version
# of "traceability" that is worth anything to them.

def _shipment(shipment_id: str):
    row = db.conn().execute(
        "SELECT * FROM shipments WHERE shipment_id=?", (shipment_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "no such shipment")
    return row


@app.get("/api/trace/{shipment_id}")
def trace(shipment_id: str):
    ship = _shipment(shipment_id)
    device = ship["device_id"]
    rows = db.records_window(device, 5000)
    if not rows:
        raise HTTPException(404, "no records for this shipment")

    good = [r for r in rows if not (r["flags"] & checks.FLAG_SENSORBAD)]
    temps = [r["temp_c"] for r in good]
    lo, hi = ship["min_c"], ship["max_c"]
    out_of_range = [r for r in good if not (lo <= r["temp_c"] <= hi)]

    # "Minutes out of range" is the number a buyer actually argues about, so it
    # is computed from the sampling interval rather than asserted.
    step_s = 300
    if len(good) > 2:
        deltas = sorted(good[i]["ts"] - good[i-1]["ts"] for i in range(1, len(good)))
        step_s = max(1, deltas[len(deltas) // 2])

    gaps = sorted(db.gaps(device), key=lambda g: g["from_seq"])
    lost = sum(g["to_seq"] - g["from_seq"] + 1 for g in gaps)
    # Always the full walk. A buyer is the one person a cached answer is
    # worthless to, and an edit before a stored mark would otherwise go unseen.
    v = verify(device, full=True)

    # The verdict is deliberately plain, and it never says "safe". We can say
    # what the record shows and whether the record is whole. Whether the food is
    # good is a judgement for the person holding it.
    if not v["ok"]:
        n = v["broken_at"]
        verdict, tone = "The record has been altered", "bad"
        because = {
            "bad_signature":
                f"Reading {n} no longer matches the signature the device put on "
                f"it. It was changed after it was recorded.",
            "column_mismatch":
                f"The value shown for reading {n} is not the value the device "
                f"signed. The display was changed; the signed reading was not.",
            "chain_break":
                f"The chain of readings breaks at reading {n}: {v['detail']}.",
            "bad_gap_signature":
                f"Readings are missing at {n} and the device never declared "
                f"them lost.",
            "missing_raw":
                f"Reading {n} was stored without the device's original bytes, "
                f"so it cannot be checked.",
        }.get(v["reason"], f"The chain of records breaks at reading {n}.")
        because += " Do not rely on this record."
        if v["reason"] == "missing_raw":
            verdict = "Part of this record cannot be checked"
    elif lost:
        verdict, tone = "Complete, with a declared hole", "warn"
        because = (f"{lost} readings no longer exist: the journey lost signal for "
                   f"longer than the device could store. The device reported "
                   f"exactly which ones, and they are listed below.")
    elif out_of_range:
        mins = len(out_of_range) * step_s // 60
        verdict, tone = "Kept, but it warmed up on the way", "warn"
        because = (f"{mins} minutes outside {lo:.0f}\u2013{hi:.0f} \u00b0C, "
                   f"peaking at {max(temps):.1f} \u00b0C. Everything else held.")
    else:
        verdict, tone = "Kept cold the whole way", "good"
        because = (f"Every one of the {len(good)} readings sat between "
                   f"{min(temps):.1f} and {max(temps):.1f} \u00b0C.")

    def iso(ts):
        return time.strftime("%d %b %Y, %H:%M", time.gmtime(ts))

    truck_nodes = db.devices_on_truck(ship["shipment_id"] and db.device(device)["truck"]) \
                  if db.device(device)["truck"] else []

    return {
        "shipment": shipment_id,
        "device": device,
        "commodity": ship["commodity"],
        "origin": ship["origin"],
        "destination": ship["destination"],
        "verdict": verdict,
        "tone": tone,
        "because": because,
        "readings": len(rows),
        "first_seen": iso(rows[0]["ts"]),
        "last_seen": iso(rows[-1]["ts"]),
        "hours": round((rows[-1]["ts"] - rows[0]["ts"]) / 3600, 1),
        "min_c": round(min(temps), 2) if temps else None,
        "max_c": round(max(temps), 2) if temps else None,
        "avg_c": round(sum(temps) / len(temps), 2) if temps else None,
        "limit_lo": lo, "limit_hi": hi,
        "minutes_out": len(out_of_range) * step_s // 60,
        "sensor_faults": len(rows) - len(good),
        "declared_lost": lost,
        "gaps": [{"from": g["from_seq"], "to": g["to_seq"]} for g in gaps],
        "chain_ok": v["ok"],
        "broken_at": v["broken_at"],
        "tamper_kind": v["reason"],
        "merkle_root": v["root"],
        "anchored": bool(db.anchors(device, 1)),
        "on_chain": False,
        "nodes_on_truck": len(truck_nodes),
        "series": [{"seq": r["seq"], "ts": r["ts"],
                    "t": None if (r["flags"] & checks.FLAG_SENSORBAD) else r["temp_c"],
                    "late": r["lag_s"] > 0} for r in rows],
    }


@app.get("/api/records/{shipment_id}")
def public_records(shipment_id: str, offset: int = 0, limit: int = 500):
    """The signed bytes themselves, so anyone can walk the chain without asking
    us whether it is whole. Public, like the rest of the buyer's surface.

    What a holder of these can check: every record's SHA-256, that each one
    carries the digest of the one before, that no reading is missing without a
    declared gap, and that the values on the page are the values signed. What
    they cannot check today is the signature itself: it is HMAC, and handing
    out the key would let anyone forge records. With the ATECC608B the
    signature becomes ECDSA and the public key can be published."""
    ship = _shipment(shipment_id)
    device = ship["device_id"]
    offset = max(0, offset)
    limit = max(1, min(limit, 2000))
    rows = db.records_raw(device, offset, limit)
    return {
        "shipment": shipment_id,
        "device": device,
        "total": db.record_count(device),
        "offset": offset,
        "limit": limit,
        "records": [{"seq": r["seq"], "raw": bytes(r["raw"]).hex() if r["raw"] else None}
                    for r in rows],
        "gaps": [{"from": g["from_seq"], "to": g["to_seq"]}
                 for g in sorted(db.gaps(device), key=lambda g: g["from_seq"])],
        "signature": "hmac-sha256 (symmetric: cannot be checked without the key)",
    }


@app.get("/api/qr/{shipment_id}")
def qr(shipment_id: str, request: Request):
    """The QR that goes on the carton. Encodes the public trace URL, nothing else
    — no data travels on the label, so the label cannot lie."""
    _shipment(shipment_id)
    url = str(request.base_url).rstrip("/") + f"/t/{shipment_id}"
    try:
        import segno
    except ImportError:
        raise HTTPException(
            503, "segno is not installed — run:  py -m pip install segno")
    buf = io.BytesIO()          # segno writes SVG as bytes, not text
    segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2,
                                    dark="#16212e", light="#ffffff")
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.get("/label/{shipment_id}")
def label(shipment_id: str, request: Request):
    """A crate label you can print and tape on a box. For the demo, this is the
    difference between describing the idea and handing someone the thing."""
    ship = _shipment(shipment_id)
    url = str(request.base_url).rstrip("/") + f"/t/{shipment_id}"
    return HTMLResponse(f"""<!doctype html><meta charset=utf-8>
<title>{shipment_id} — crate label</title>
<style>
 @page{{size:100mm 70mm;margin:0}}
 body{{margin:0;font:13px/1.4 "Segoe UI",system-ui,sans-serif;color:#16212e}}
 .l{{width:100mm;height:70mm;box-sizing:border-box;padding:6mm 7mm;
     display:flex;gap:6mm;align-items:center;border:1px solid #dbe4ec}}
 .q{{width:34mm;flex:none}} .q img{{width:100%;display:block}}
 h1{{margin:0 0 1mm;font-size:17px;letter-spacing:.3px}}
 .s{{font-size:11px;color:#5a7480;margin-bottom:3mm}}
 .id{{font:13px Consolas,monospace;font-weight:700;letter-spacing:.5px}}
 .c{{font-size:10.5px;color:#5a7480;margin-top:2mm;line-height:1.35}}
 @media print{{.l{{border:0}} .no{{display:none}}}}
 .no{{padding:10px 14px;font-size:12px;color:#5a7480}}
</style>
<div class=no>Ctrl&nbsp;+&nbsp;P to print. Tape it to the crate.</div>
<div class=l>
  <div class=q><img src="/api/qr/{shipment_id}" alt="QR"></div>
  <div>
    <h1>AnnaChain</h1>
    <div class=s>{ship['commodity']} &middot; {ship['origin']} &rarr; {ship['destination']}</div>
    <div class=id>{shipment_id}</div>
    <div class=c>Scan to see every temperature reading of this
      consignment, and to check for yourself that none of them has been
      changed since it was recorded.</div>
  </div>
</div>""")


@app.get("/t/{shipment_id}")
def trace_page(shipment_id: str):
    f = STATIC / "trace.html"
    if not f.exists():
        return JSONResponse({"error": "trace page not found"}, 500)
    return FileResponse(f)


@app.post("/api/reset")
def reset(who: str = Depends(operator)):
    """Wipe everything, for a clean demo run."""
    db.init(reset=True)
    verifier.__init__()
    engine.state.clear()
    last_ingest_at.clear(); last_batch_n.clear()
    silence_start.clear(); recovering.clear(); _diagnosed.clear()
    return {"ok": True}


# ── the dashboard ────────────────────────────────────────────────────────
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(status_code=204)


if STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    f = STATIC / "index.html"
    if not f.exists():
        return JSONResponse({"error": "dashboard not found"}, 500)
    return FileResponse(f)
