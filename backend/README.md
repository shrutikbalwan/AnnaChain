# The server half

Everything the deck promises *after* a record reaches the cloud: the eight
checks, the database, the alert rules, the chain verification, the Merkle root,
GS1 EPCIS output, and a dashboard that shows a truck going dark and coming back.

It runs with no hardware at all.

---

## Run it

```powershell
py -m pip install -r backend/requirements.txt
py -m uvicorn backend.app:app --port 8000
```

Open **http://127.0.0.1:8000**. It asks you to sign in.

On the very first run the server creates one account and prints it once:

```
username: operator
password: annachain
```

**Change it** with `POST /api/password` before anyone else can reach the
machine. The buyer's trace page at `/t/<shipment>` stays public — that is the
point of it — and the node's own ingest path is authenticated by the signature
on every record, not by a password. A node has no password to lose.

**Start every demo from a clean database** (`make clean`, or delete
`backend/annachain.db*` and `backend/ledger.jsonl`). A database left over from
testing shows the judges whatever was done to it.

Now give it a trip. In a second terminal, first build the capture generator
(same compiler you already used), and make the capture right before you replay
it — the trip ends at the moment it is captured, and the server refuses
readings older than a node could have held them:

```
g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp tools/dump.cpp -o dump.exe
dump.exe 1000 350 | Out-File demo.capture -Encoding ascii
```

then replay it:

```powershell
py backend/feed_sim.py demo.capture --reset
```

Enrolling the capture's devices is an operator action, so the replayer signs in
— as `operator` / `annachain` unless you pass `--user` and `--password`.
`--reset` wipes the server's data first. Feeding records never needs a login: a
node proves itself with the signature on every record.

The capture says **ethylene: not fitted**, because the board has no ethylene
sensor. `dump.exe ... --ethylene` simulates one; do that only if you are going
to say out loud that the ethylene line is invented.

Watch the browser. Records arrive one at a time, the temperature holds around
4 °C, a door opens at a checkpoint and it spikes, alerts fire — then the truck
goes **SILENT**. Nothing arrives at all for twelve seconds. Then the backlog
lands in batches, the status goes to **CATCHING UP**, and the tail of the graph
redraws in orange: *held on the device, delivered late*.

`--silence 30` makes the blind spot longer if you want more time to talk over it.

---

## Three nodes, and one of them is lying

The slide-2 claim *nodes watch each other* has its own generator:

```
g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp tools/fleet.cpp -o fleet.exe
.\fleet.exe 300 120 | Out-File fleet.capture -Encoding ascii
py backend/feed_sim.py fleet.capture --reset
```

Three crates in one reefer. An hour in, node B's calibration starts walking away
— slowly, so no single reading looks wrong — until it reads about 10 °C while
the other two read 4 °C.

A naive system calls that spoilage and sends a driver to check a load that is
perfectly fine. Watch what this one does instead: **All nodes on this truck**
shows B's line peeling away from the other two, B is badged SUSPECT, and **its
temperature alerts stop firing**. The load is not reported as spoiling because
one thermometer drifted.

The distinction is visible in the same chart: when a door is actually opened at
a checkpoint, *all three lines spike together*. That is how a real excursion is
told apart from a failing sensor, and it is why more than one node per truck
earns its place in the BOM.

A node is only called suspect after it has disagreed for three consecutive
readings, so a single noisy sample cannot flag it. With only two nodes the
system says they disagree and names no culprit — which is the honest answer,
because with two thermometers there is no way to know which one is right.

---

## Proof at the fork — the QR on the carton

`/t/<shipment>` is the page a buyer reaches by scanning the crate. No login, no
key: they can check the record without asking anyone's permission, which is the
only kind of traceability that is worth anything to them.

It states plainly what the record shows — *kept cold the whole way*, *warmed up
on the way*, *complete with a declared hole*, or *the record has been altered*
(and whether a reading was forged or only its display was changed). The verdict
always comes from a full walk, never from a stored mark. It deliberately never
says the food is safe: it says what the sensors recorded and that nothing has
been changed since.

**Re-check every reading now** does two separate things, and the page says which
is which:

1. **On the buyer's phone.** It downloads the signed 84-byte records from
   `GET /api/records/<shipment>` (public, paginated) and walks them itself:
   every record's SHA-256, that each carries the digest of the one before, that
   no reading is missing without a declared gap, and that every temperature on
   the page is the one the device signed. It uses WebCrypto where the browser
   offers it and a plain JavaScript SHA-256 otherwise (WebCrypto is missing on
   plain `http://` from a phone).
2. **On our server.** `GET /api/verify/<device>?full=true`, which is the only
   place the signature can be checked today — see the limitations below.

`/label/<shipment>` is a printable 100 × 70 mm crate label with the QR on it.
Print one, tape it to a box, and hand a judge their own phone.

**To scan it from a phone, the laptop has to be reachable on the network:**

```powershell
py -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
ipconfig          # find your IPv4 address, e.g. 192.168.1.14
```

Then open `http://192.168.1.14:8000/label/AC-26232001` **on the laptop**. The QR
is generated from the address you are browsing, so it will encode the same IP
and the phone will resolve it. A QR made while browsing `127.0.0.1` points the
phone at itself and will not work — that is the one mistake to avoid at the
venue, and it is worth testing the day before.

---

## What each file does

| File | What it is |
|---|---|
| `app.py` | The API. Enrolment, ingest, last-ack, gap notice, state, verify, anchor, EPCIS, public records. |
| `checks.py` | The eight checks, and the record layout. Must match `lib/ac/ac_record.h`. |
| `alerts.py` | The alert rules, including cross-node disagreement. |
| `db.py` | SQLite schema and queries. Same shape PostgreSQL + TimescaleDB gets later. |
| `feed_sim.py` | Replays a captured trip, with the silence in the right place. |
| `../tools/fleet.cpp` | Three nodes on one truck, one with a drifting sensor. |
| `bridge_serial.py` | USB ↔ API bridge, for when the board arrives. |
| `static/index.html` | The dashboard. |
| `static/trace.html` | The public page a buyer reaches from the QR. |
| `auth.py` | PBKDF2 password hashing and session tokens. |
| `ledger.py` | The anchoring interface: a working local hash-linked ledger, and the Fabric adapter it will be swapped for. |
| `shelflife.py` | Q10 shelf-life kinetics and explainable anomaly classification. |
| `tests/` | The pytest suite. One file per defect the verification found, plus the checks, alerts, shelf life, EPCIS and auth. |

---

## The two ideas worth understanding

**A batch of one is normal; a batch of many is a backlog.** The firmware calls
`resync()` after every reading, so online it sends one record per request. It
only batches when it is draining a buffer. The server uses that, plus a gap in
arrival times, to mark records as *held on the device* — and it needs nothing
from the node to do it. That is why the orange section of the graph is real
information and not a label the feeder painted on.

**Trip time and wall time are different clocks.** A record carries the moment it
was *taken*. It arrives whenever the network allows. The dashboard plots by trip
time, so the graph is continuous across the outage — which is the whole point.
There is no hole in the line, only a change of colour.

---

## Three access levels, and why

| Level | Who | How they are checked |
|---|---|---|
| **Public** | a buyer with a carton | nothing. `/t/<shipment>` is open on purpose — a record you have to ask permission to see is not traceability |
| **Device** | a node uploading, or declaring a gap | the signature on every record and on every gap notice. Not a password |
| **Operator** | the dashboard, enrolling a device | a login. It shows who is shipping what, which is commercial information |
| **Admin** | replacing a device's key | an admin login, and a row in the `audit` table. A key is never replaced silently |

---

## Tests

```
py -m pip install -r backend/requirements-dev.txt
py -m pytest backend/tests -q          # or: make backend-test
make test                              # firmware selftest and the backend suite
```

Each defect the hostile verification found (docs/VERIFY.md) has a test file
named for it — `test_tamper.py`, `test_gap.py`, `test_enrolment.py`,
`test_public_verify.py`, `test_time.py` — written to fail against the old code
first. The rest cover the eight checks, the alert rules, the shelf-life maths,
EPCIS and the authentication boundary.

---

## Shelf life: the honest version

`shelflife.py` uses **Q10 kinetics**, the standard model for temperature-driven
deterioration:

```
life(T) = life_ref x Q10 ^ ((T_ref - T) / 10)
```

Every reading consumes a slice of the remaining life. Warm stretches consume it
faster, which is exactly why a 25-minute excursion matters.

The parameters are literature-typical values per commodity and **have not been
validated against storage trials by this project** — the API says so on every
response, and so should you. Validating one commodity is a named next step.

Two temperature ranges are kept apart deliberately: the **agreed** range the
shipper contracted to hold, and the commodity's **ideal** range. Alerts fire on
the agreed range, because that is the one someone signed. When the agreed range
is warmer than ideal — 2–8 °C for grapes that would prefer 0 °C — the response
says so, because the load is losing life even while technically in compliance.

Anomaly classification is **rules over the shape of the curve**, not a
classifier. A door opening, a compressor failing and a sensor drifting look
different: fast rise then fast recovery; slow rise that never recovers;
divergence from the other nodes on the same truck. Each verdict can be
explained in one sentence to the person who has to act on it, which a softmax
output cannot. No model was trained on simulated data and then described as
accurate.

---

## Honest about what this is not

- **The QR proves the record, not the food.** The trace page says so in as many
  words. Anything that claims a sensor log certifies food safety is overselling.
- **The ledger is local, not distributed.** `ledger.py` has a real,
  working, append-only hash-linked ledger and a Fabric adapter that reports
  itself unavailable until a network exists. Every anchor response names its
  backend and says `"distributed": false`. A local ledger is tamper-evident;
  it is not independent, because we host it. That independence is exactly what
  Fabric adds and why it is in the design.
- **SQLite, not PostgreSQL + TimescaleDB.** The schema is written for the move
  and `records` is the hypertable candidate, but the finale build is not done.
- **Signatures are HMAC — a scope decision, not an oversight** (see
  [`docs/CRYPTO.md`](../docs/CRYPTO.md), decided 30 Sep 2026: "driver now,
  format later"). The ATECC608B is on the BOM and its driver
  (`lib/ac/ac_atecc.*`, `IEcdsaSigner`) exists, but a P-256 signature is 64
  bytes and a v1 record has 32, so ECDSA is a record-format change, specified
  there and not built. Records are format v1: 84 bytes, no version byte,
  identified by length; any later format starts with a version byte, and the
  server refuses a format it does not know by name. HMAC is symmetric, so the
  server holds the same key. That is the weakness the ATECC608B removes. Two
  things follow from it today:
  - **A buyer cannot check a signature.** The trace page re-derives the hash
    chain on the phone, but the signature check happens on our server, so the
    buyer is trusting us for that part. The page says so.
  - **Whoever holds the key and the database can rewrite history.** That is us.
    Someone with both could re-sign and re-chain every record, and every check
    here would pass. What would catch it is an anchor we do not control — which
    the local ledger is not (see below). With ECDSA the server never holds a
    signing key, and this weakness goes away.
- **The phone cannot see an edit to the very last record** when the signed
  bytes and the displayed value are changed together: there is no later record
  whose link would break, and the two still agree. The server's signature
  check catches it; the phone alone does not.
- **Enrolment is an operator login, not a secure element.** `/api/register`
  needs a login, a key is set once, and replacing it needs `rotate=true`, an
  admin, and leaves a row in `audit`. The keys a device has had are kept in
  `device_keys`, so records signed before a rotation still verify. But the
  audit table lives in the same database, so someone with database access can
  edit it too.
- **Resumable verification marks live in the database.** `/api/verify` without
  `?full=true` trusts a mark to skip what it already checked, and the marks are
  as editable as anything else in there. So nothing a buyer sees, nothing on
  the operator dashboard, and no anchor ever uses a mark: those always walk
  every record. The mark exists for API callers who ask for it.
- **Databases from before this change cannot be fully verified.** They did not
  keep the device's raw bytes. The column is added on start-up, but old rows
  have nothing in it, and `verify` reports each one as `missing_raw` rather than
  passing it. There is nothing to backfill it from.
- **Gap notices cross the truck gateway** (done). A node's signed notice goes
  over LoRa as a gap frame, waits in the gateway's FIFO with the records, and is
  handed upstream unchanged and in order; a notice lost to gateway overrun is
  counted (`gapsDropped`). `backend/tests/test_gateway_gap_e2e.py` runs the
  C++ node and gateway and checks what the buyer sees.
- **Records the gateway itself drops are not declared.** When the *gateway's*
  buffer overruns (the node was in LoRa range but the cab had no signal for
  longer than the gateway can hold), the lost records are counted on the
  gateway, but nothing signed tells the server. The node has already been
  acknowledged hop-by-hop, so it does not resend, and the server waits for the
  first missing sequence number. Closing this needs the node to reconcile
  against the server's last-ACK through the gateway, which the LoRa path does
  not do yet.
- **Clock checks are bounds, not a time source.** A reading more than 60 s
  ahead of the server, more than 30 days old, or earlier than the reading
  before it is refused. A clock that is wrong by less than that (a few hours
  behind, say) is not caught, and the node still has no NTP or RTC. The 30 days
  is flash capacity (about 14 days at 5-minute sampling) plus a gateway buffer.
- **Ethylene is simulated only when asked for.** The capture tools default to
  "not fitted", like the board. With `--ethylene` the simulator invents a
  curve, and the record format has no flag saying so, so the server cannot
  tell; the dashboard's note under the chart says where such values can only
  come from.
- **Failed logins back off** (done). After 5 failures in a row for a username,
  or from one address, sign-in is refused for 30 s, doubling with each further
  failure up to 15 minutes; the right password is refused too while the lock
  lasts. The wait is in the `Retry-After` header and in `retry_after_s`, and the
  sign-in form shows it. What it does not do: the counters live in memory, so a
  restart clears them and several worker processes would each keep their own;
  behind a reverse proxy every request comes from the proxy's address; and
  anyone can lock the real operator out for up to 15 minutes by failing on
  purpose, which is the usual price of a per-username lock.
- **Chart.js is vendored** (done). `backend/static/chart.umd.min.js` is
  Chart.js 4.4.1, checked against the SRI hash cdnjs publishes, with its MIT
  licence beside it. The dashboard never needs the CDN; the hand-drawn
  renderer stays as a second line of defence.
- **The deck is not in the repository.** `docs/VERIFY.md` section 7 reads
  `docs/SIH2026_26232_AnnaChain_OfficialFormat.pptx`, and no file of that name
  exists yet. The only 26232 deck found (`SIH2026_26232_SecureHarvest_OfficialFormat.pptx`)
  has 6 slides but still carries the old name on slides 1, 2 and 5.
- **Calibration (check 7) is implemented but unpopulated.** Each device carries
  a calibration date and an EN 13486 interval; a lapsed sensor raises an alert
  and its readings are marked as uncertified. The readings are still stored —
  refusing them would throw away the only record of the journey. What does not
  exist yet is a real calibration registry with real certificates in it.
- **The ethylene rule is a rise against the trip's own baseline**, not an
  absolute ppb threshold, because we have not chosen a sensor and will not quote
  a calibrated number we cannot measure.

---

## When the board arrives

```powershell
pio run -e node_mock -t upload
py backend/bridge_serial.py COM5
```

Same dashboard, real device. Press **BOOT** on the board and the status goes
SILENT while the node keeps logging; press it again and the gap fills in orange.

---

## Verification that scales

`/api/verify/<device>` resumes from the last stored **mark** — a sequence
number whose digest and Merkle root were computed earlier — and only re-checks
what has arrived since. Walking every record is fine at four thousand and wrong
at ninety days' worth.

`?full=true` forces the complete walk from record 1. That option is what makes
the shortcut honest: an auditor who does not trust our marks never has to. The
buyer's page, its verdict, the operator dashboard, and every anchor always
use it (about 60 ms for 4,000 records).

Every record walked is re-derived from the 84 bytes the device sent: its
digest recomputed, its signature re-checked against the key that was current
for that sequence number, its link compared against the *recomputed* digest of
the record before, and every stored column (`ts`, `temp_c`, `rh_pct`,
`c2h4_ppb`, `flags`, `batt_pct`, `digest`, `prev`, `sig`) compared against a
fresh parse. A failure names its kind: `bad_signature` (the signed bytes were
changed), `column_mismatch` (the display was changed, the signed reading was
not), `chain_break` (a reading is missing or out of place), `bad_gap_signature`
(a hole the device never declared), or `missing_raw`.

---

## Demo on bad Wi-Fi

Chart.js is vendored in `backend/static/`, so the dashboard never touches the
network for it. If that file were ever missing it would try the CDN, and if
neither is reachable it draws the lines itself. Nothing breaks.
