# AnnaChain — master verification prompt

Paste everything below into a fresh AI session (or work through it by hand) to
check whether this project is actually built or only claimed to be.

It is written to be **hostile**. The point is to find what is broken, not to
confirm what is hoped. A verification pass that finds nothing is usually a
verification pass that did not look.

---

## THE PROMPT

> You are verifying a Smart India Hackathon 2026 entry: **AnnaChain**, problem
> statement **26232** (Low-Cost IoT Blockchain Nodes for Farm-to-Fork
> Traceability, Ministry of Food Processing Industries, **Hardware** category).
>
> Your job is to determine, by running things, whether each claimed component
> actually works — not whether the code looks plausible. Assume every claim is
> unproven until a command you ran proves it.
>
> **Rules:**
> 1. Run the commands. Do not read code and conclude it "should work".
> 2. Check the *values*, not just that a command exited 0 or returned HTTP 200.
>    An endpoint returning 200 with wrong data is a failure.
> 3. Section 8 lists limitations that are deliberate and documented. Do not
>    report those as defects. Anything else that differs from the expected
>    output below is a defect — report it with the command and the output.
> 4. If a command fails, report the failure. Do not work around it silently.
>
> Work through sections 1–7 in order and produce a table at the end:
> component, pass/fail, evidence.

---

## What the project claims to be

A cheap sensor node that rides with a crate of produce, measures temperature,
humidity and ethylene, and **writes every reading to its own flash before it
touches a radio**. When the truck loses signal the node keeps logging; when the
signal returns it uploads only what the server is missing. Every record is
hash-chained to the one before it and signed inside the device, so a reading
changed afterwards breaks the chain and is refused.

The claims that must hold:

| # | Claim | Where it must be proved |
|---|---|---|
| C1 | Nothing is transmitted before it is stored | §2 |
| C2 | An outage delays the record, it never puts a hole in it | §2, §4 |
| C3 | A record edited after signing is detected and refused | §2, §6 |
| C4 | If the flash overruns, the loss is **declared**, never hidden | §2 |
| C5 | A drifting sensor is flagged as a suspect sensor, not reported as spoilage | §4 |
| C6 | The truck gateway cannot forge a record | §2 |
| C7 | Eight checks run before anything is stored | §4 |
| C8 | Commercial data needs a login; the buyer's page does not | §3 |
| C9 | A buyer can re-verify the whole chain themselves | §6 |

---

## 1. Build everything from scratch

Delete any previous build first. From the project root:

```bash
CORE="lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp"

g++ -std=gnu++17 -Wall -Wextra -DAC_LOG_CAPACITY=4096 -Ilib/ac $CORE tools/selftest.cpp -o selftest
g++ -std=gnu++17 -Wall -DAC_LOG_CAPACITY=4096 -DAC_NATIVE=1 -Ilib/ac $CORE native/main.cpp -o demo
g++ -std=gnu++17 -Wall -DAC_LOG_CAPACITY=4096 -Ilib/ac $CORE tools/dump.cpp  -o dump
g++ -std=gnu++17 -Wall -DAC_LOG_CAPACITY=4096 -Ilib/ac $CORE tools/fleet.cpp -o fleet
```

On Windows add `.exe` to each `-o` name.

**Expected:** four binaries, **no warnings**, no errors.

Also confirm it builds under strict C++17, which is where portability problems
surface:

```bash
g++ -std=c++17 -Wall -DAC_LOG_CAPACITY=4096 -Ilib/ac $CORE tools/selftest.cpp -o /tmp/strict
```

**FAIL if:** any warning appears, or strict C++17 fails while gnu++17 passes.

---

## 2. Firmware behaviour — 92 automated checks

```bash
./selftest
```

**Expected:** `92 checks, 0 failed` and `ALL GOOD`. (70 originally; 8 were
added for the signed gap notice and the clock base, and 14 for gap notices
crossing the truck gateway.)

Read the section names as they scroll. They must include, and all pass:

- SHA-256 against the published vectors *(if this fails, nothing else means anything)*
- Record encoding — including a sub-zero temperature surviving as signed
- Store first, transmit second — **C1**
- 29 hours dark, then catch up — **C2**
- The link dies in the middle of the catch-up
- Power cycle in the middle of the outage
- Someone edits a stored reading — **C3**
- Someone replays an old record
- A very long outage — longer than the flash — **C4**
- A gap notice is signed by the device — **C4**, **C6**
- The board's clock starts on the date it claims
- The sensor stops answering
- Nine gateway sections, including **"A declared gap crosses the gateway"**,
  **"The gateway cannot alter a gap notice"** and **"A gap notice lost to gateway
  overrun is counted"**, ending with **"The gateway cannot make a record up"** — **C4**, **C6**

**FAIL if:** the count is below 92, anything is red, or a section above is missing.

Then the server's own suite:

```bash
python3 -m pip install -r backend/requirements-dev.txt
python3 -m pytest backend/tests -q
```

**Expected:** every test passes (124 at the time of writing; the browser
tests skip on a machine without Chrome or Playwright, and the gateway
end-to-end test skips without a C++ compiler). **FAIL if** any test
fails, or `backend/tests/` is missing.

```bash
./demo
```

**Expected:** ends with `DEMO PASSED`, and on the way shows 1000 records
delivered, 350 held during the outage, 350 recovered in order, chain verified,
then a deliberate edit that breaks the chain at that exact record.

---

## 3. The authentication boundary

Start a server on a **fresh database**:

```bash
rm -f backend/annachain.db* backend/ledger.jsonl
python3 -m uvicorn backend.app:app --port 9000
```

It must print a one-time account on first start. Then, from another terminal:

```bash
B=http://127.0.0.1:9000
for p in /api/state /api/shipments /api/ledger; do
  echo "$p -> $(curl -s -o /dev/null -w '%{http_code}' $B$p)"
done
curl -s -o /dev/null -w "anchor -> %{http_code}\n" -X POST $B/api/anchor/1
curl -s -o /dev/null -w "bad password -> %{http_code}\n" -X POST $B/api/login \
  -H 'Content-Type: application/json' -d '{"username":"operator","password":"wrong"}'
curl -s -o /dev/null -w "register    -> %{http_code}\n" -X POST $B/api/register \
  -H 'Content-Type: application/json' -d '{"device":1,"key_hex":"00112233445566778899aabbccddeeff"}'
```

**Expected: every one of these is `401`.** — **C8**

Now the public surface, still with no token:

```bash
curl -s -o /dev/null -w "dashboard html -> %{http_code}\n" $B/
curl -s -o /dev/null -w "unknown trace  -> %{http_code}\n" $B/api/trace/nope
```

**Expected:** `200` and `404`. **FAIL if either returns 401** — the buyer's
surface must never require a login, and a 401 there would break claim C8 in the
other direction.

---

## 4. Load a real three-node trip and check the data

```bash
./fleet 300 120 > fleet.capture
python3 backend/feed_sim.py fleet.capture --base $B
```

Make the capture right before feeding it: the trip ends at the moment it is
captured, and the server refuses readings older than a node could have held
them. `feed_sim.py` signs in as the first-run operator to enrol the devices.

**Expected from the capture:** 1260 `R` lines, 3 `K` lines, 3 `L` lines.

Sign in and inspect:

```bash
TOK=$(curl -s -X POST $B/api/login -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"annachain"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['token'])")
A="Authorization: Bearer $TOK"

curl -s -H "$A" $B/api/shipments   | python3 -m json.tool | head -30
curl -s -H "$A" "$B/api/state?device=639836161" | python3 -c "import json,sys;s=json.load(sys.stdin);print(s['stored'],s['recovered'],s['declared_lost'])"
curl -s $B/api/verify/639836161    | python3 -m json.tool
curl -s -H "$A" $B/api/ledger      | python3 -m json.tool
```

**Expected values — check each one:**

| Thing | Must be |
|---|---|
| shipments | exactly **3** |
| records each | **420** |
| devices flagged suspect | exactly **1** (node B) — **C5** |
| node A stored / recovered / lost | **420 / 120 / 0** — **C2** |
| verify → `ok` | `true`, `records` **420** |
| ledger → `ok` | `true`, `distributed` **false** |

**FAIL if:** zero nodes are suspect (the diagnosis is not running), or more than
one is (it is flagging honest nodes), or `recovered` is 0 (the outage was not
detected as an outage).

Check the eight checks are really eight — **C7**. Open `backend/checks.py` and
confirm each numbered check has a body, in particular **check 7 (calibration)**,
which was an empty placeholder in earlier versions. Then prove it:

```bash
python3 - <<'EOF'
import sys, time, struct, hashlib, hmac; sys.path.insert(0,'.')
from backend import checks
KEY=b"k"
def rec(dev,seq,ts,t):
    body=struct.pack("<IIIhHHBB",dev,seq,ts,int(t*100),9000,0xFFFF,0,90)+b"\x00"*32
    return body+hmac.new(KEY,hashlib.sha256(body).digest(),hashlib.sha256).digest()
now=int(time.time())
v=checks.Verifier(); v.check(rec(1,1,now,4.2),KEY,False,cal_due=time.time()-90*86400)
print("lapsed calibration flagged:", 1 in v.stale_calibration)
w=checks.Verifier(); w.check(rec(2,1,now,4.2),KEY,False,cal_due=time.time()+90*86400)
print("in-date NOT flagged      :", 2 not in w.stale_calibration)
EOF
```

**Expected:** both lines print `True`.

---

## 5. Verification that scales

```bash
curl -s $B/api/verify/639836161 | python3 -c "import json,sys;d=json.load(sys.stdin);print('checked',d['checked_now'],'resumed from',d['resumed_from'])"
curl -s $B/api/verify/639836161 | python3 -c "import json,sys;d=json.load(sys.stdin);print('checked',d['checked_now'],'resumed from',d['resumed_from'])"
curl -s "$B/api/verify/639836161?full=true" | python3 -c "import json,sys;d=json.load(sys.stdin);print('checked',d['checked_now'],'full walk')"
```

**Expected:** the second call checks **0** records (it resumed from a stored
mark), and `?full=true` re-walks **420**. If both calls walk everything, the
checkpointing is not working.

---

## 6. The buyer's page — C9

```bash
TS=$(curl -s $B/api/trace/AC-26232001 | python3 -c "import json,sys;print(json.load(sys.stdin)['series'][200]['ts'])")
curl -s -X POST -H "$A" -H 'Content-Type: application/json' \
  -d "{\"shipment_id\":\"AC-26232001\",\"place\":\"Dhule checkpoint\",\"biz_step\":\"inspecting\",\"ts\":$TS}" $B/api/checkpoint
curl -s $B/api/epcis/AC-26232001 | python3 -c "
import json,sys
for e in json.load(sys.stdin)['epcisBody']['eventList']:
    print(e['action'], e['bizStep'].split(':')[-1])"
```

**Expected:** at least **three** events — `commissioning`, the checkpoint you
just added, and `receiving`. **FAIL if adding a checkpoint makes the
commissioning event disappear** (that was a real bug).

Then open in a browser:

- `$B/t/AC-26232001` — verdict should read **"Kept, but it warmed up on the
  way"**, with roughly **25 minutes outside 2–8 °C, peaking near 14.5 °C**.
  Press **Re-check every reading now**: it must report all **420** verify.
- `$B/label/AC-26232001` — a printable label with a QR that actually renders.
- `$B/` — sign in; the overlay must disappear, three consignments in the
  dropdown, node B marked SUSPECT, shelf-life card showing about **96.5%** and
  **57.9 days**, and the truck chart showing one line drifting away from two.

**Check the browser console on all three pages. Expected: no errors at all.**
Chart.js is served from `backend/static/`, so there is no 404 for it any more;
one now is a failure.

---

## 7. The deck

```bash
python3 tools/check_deck.py docs/SIH2026_26232_AnnaChain_OfficialFormat.pptx
```

It looks in grouped shapes, tables, speaker notes, layouts, masters and document
properties, not just text boxes, and in the PDF of the same name if it is there
(`pip install pypdf` for that part).

**Expected:** `slides: 6` (the official limit) and `old name present: False`,
exit status 0. **FAIL if** the deck is missing: that is not a pass by default.

---

## 8. Known and intended — do NOT report these as defects

These are documented in `backend/README.md` and stated in the API responses.
Reporting them as bugs is a false positive.

| Thing | Why it is deliberate |
|---|---|
| Signatures are **HMAC, not ECDSA** | The ATECC608B is not fitted yet. The interface is already the secure element's |
| The ledger is **local, not distributed** | `ledger.py` has a real hash-linked ledger and a Fabric adapter that reports itself unavailable. Every anchor returns `"distributed": false` |
| **SQLite, not PostgreSQL + TimescaleDB** | Schema is written for the move; `records` is the hypertable candidate |
| **No ethylene sensor is read** | The field transmits *not fitted*. The part has not been chosen, on purpose |
| Shelf-life parameters are **not validated** | Literature-typical Q10 values. Every response carries the caveat |
| `LoraRadio` **receives nothing** | Stub until the SX1262 arrives. The gateway logic above it is fully tested |
| The clock starts from a **compiled-in date** | No NTP or RTC yet; the server's timestamp check is what catches a wrong one |
| A calibration **registry** does not exist | The check works; there are no real certificates to put in it yet |

---

## 6b. The attacks from the first verification — they must now fail

These three were proved against an earlier build. Each must now be caught.

```bash
# 1. Edit a displayed reading straight in the database (C3)
python3 - <<'EOF'
import sqlite3; c = sqlite3.connect("backend/annachain.db")
c.execute("UPDATE records SET temp_c = temp_c + 20 WHERE device_id=639836161 AND seq=200"); c.commit()
EOF
curl -s "$B/api/verify/639836161?full=true" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['ok'],d['broken_at'],d['reason'])"
curl -s $B/api/trace/AC-26232001 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['verdict'],'|',d['chain_ok'])"
python3 - <<'EOF'
import sqlite3; c = sqlite3.connect("backend/annachain.db")
c.execute("UPDATE records SET temp_c = temp_c - 20 WHERE device_id=639836161 AND seq=200"); c.commit()
EOF
```

**Expected:** `False 200 column_mismatch`, then `The record has been altered | False`.
After the second script, `?full=true` is `ok: true` again.

```bash
# 2. Re-key a live device and fake a gap, with no login (C6)
curl -s -o /dev/null -w "re-register -> %{http_code}\n" -X POST $B/api/register \
  -H 'Content-Type: application/json' -d '{"device":639836163,"key_hex":"6174746163686b65792d303030303030303030303030303030303030303030"}'
curl -s -o /dev/null -w "fake gap    -> %{http_code}\n" -X POST $B/api/gap \
  -H 'Content-Type: application/json' -d '{"device":639836163,"from_seq":421,"to_seq":421,"mac":"00"}'
curl -s -o /dev/null -w "re-key w/ login -> %{http_code}\n" -X POST -H "$A" $B/api/register \
  -H 'Content-Type: application/json' -d '{"device":639836163,"key_hex":"6174746163686b65792d303030303030303030303030303030303030303030"}'
```

**Expected:** `401`, `401`, and `409` — even an operator cannot replace a key
without `rotate=true` and an admin role. Node C's trace still says `chain_ok: true`.

**3. An honest gap (C4)** is covered by `backend/tests/test_gap.py`: records
1–3, a signed gap for 4–5, then 6–10 must verify clean and read *"Complete, with
a declared hole"*. Section 6c runs it through the truck gateway.

```bash
# 4. Guess the operator password (do this last: it locks this address for 30 s)
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code} " -X POST $B/api/login \
    -H 'Content-Type: application/json' -d '{"username":"operator","password":"guess'$i'"}'
done; echo
curl -s -D - -o /dev/null -X POST $B/api/login -H 'Content-Type: application/json' \
  -d '{"username":"operator","password":"annachain"}' | grep -i retry-after
```

**Expected:** `401 401 401 401 429 429`, then a `Retry-After:` header on the
*correct* password: during the lock it is refused too.

---

## 6c. A declared gap through the truck gateway — C4 on the real path

On the truck, a node never talks to the server. It talks LoRa to the gateway in
the cab, and the gateway talks to the server. A gap notice that only works when
the node reaches the server directly is true in the harness and false in the
field. So this runs the story **through** the gateway.

`dump --gateway` runs the real `Node` and `Gateway` code. The crate is out of
LoRa range long enough for its 4,096-record flash to overrun; it comes back
while the cab still has no signal, so its signed gap notice has to wait in the
gateway's buffer; then the uplink returns. What the capture holds is what the
gateway sent upstream.

Use a **fresh database** (stop the server, `rm -f backend/annachain.db* backend/ledger.jsonl`,
start it again): this capture uses node A's device id.

```bash
./dump --gateway 50 4200 > gw.capture
grep -nE '^(G|#)' gw.capture
python3 backend/feed_sim.py gw.capture --base $B --rate 5000 --silence 1
curl -s "$B/api/verify/639836161?full=true" | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['ok'],d['records'],d['declared_gaps'])"
curl -s $B/api/trace/AC-26232001 | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['verdict'],'| lost',d['declared_lost'],'| chain',d['chain_ok'])"
```

**Expected:**

- `dump` reports on stderr `gateway holding 4109 frames; gap notices heard: 1`, then
  `forwarded 4158 records and 1 gap notice(s), dropped 0 records and 0 notices`.
- In the capture, the only `G` line is `G 639836161 51 154 <64 hex>`, right after
  `# online` and **before** every record after the hole.
- `True 4158 [{'from': 51, 'to': 154}]`
- `Complete, with a declared hole | lost 104 | chain True`

**FAIL if** there is no `G` line (the gateway dropped the notice), the verdict
says *altered*, or the feed reports that the server refused the notice.

The firmware side is in `./selftest`: *"A declared gap crosses the gateway"*
(byte for byte, held while the uplink is down, verifies against the device key
at the server), *"The gateway cannot alter a gap notice"*, and *"A gap notice
lost to gateway overrun is counted"*. `backend/tests/test_gateway_gap_e2e.py`
runs this same path automatically, including a gateway that widens the hole and
is refused.

---

## 9. What is genuinely still missing

Not defects — unbuilt. Confirm these are still absent rather than quietly
half-done, which is worse than absent. `backend/README.md` lists each one, and
the other remaining limitations, under *Honest about what this is not*.

1. No running Fabric network (adapter only).
2. A buyer's phone cannot check a signature until the ATECC608B makes it ECDSA.
3. Records the *gateway* drops on its own overrun are counted there but not
   declared to the server.

Done since the first verification, and no longer to be reported as missing:
backend tests, login back-off, vendored Chart.js, gap notices through the
gateway.

---

## 10. Report like this

| Component | Pass / Fail | Evidence |
|---|---|---|
| Build, no warnings | | |
| 78 firmware tests | | |
| Backend test suite | | |
| C1 store before transmit | | |
| C2 outage recovery | | |
| C3 tamper detected | | |
| C4 gap declared | | |
| C5 suspect sensor | | |
| C6 gateway cannot forge | | |
| C7 eight checks, incl. calibration | | |
| C8 auth boundary | | |
| C9 buyer can re-verify | | |
| Scalable verification | | |
| EPCIS events | | |
| Old attacks now fail (6b) | | |
| Deck: 6 slides, correct name | | |

For each failure give the command, the output you got, and the output expected.
