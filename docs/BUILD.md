<!-- The phased build brief (Phases 0–5), as pasted into the build session (30 Sep 2026). Reproduced as received. It was carried out on branch build-brief-phases (PR #1). -->

# AnnaChain — the build brief, in phases

Fifteen items, six phases, a gate between each. Paste everything below into the
agent session that holds the repository.

Baseline measured at `3570c4d`: **92 firmware checks**, **124 backend tests**,
`make demo` → DEMO PASSED. Every phase must end with those still green.

---

## THE PROMPT

> You are finishing the software of **AnnaChain**, an SIH 2026 entry for
> problem statement 26232 (farm-to-fork cold-chain traceability, MoFPI,
> **Hardware** category). Components arrive in 2–3 days. Your entire scope is
> software: nothing below needs a part to have arrived.
>
> Work in the phases given. **Do not start a phase until the previous phase's
> gate has passed**, and do not reorder items within a phase where an order is
> stated.
>
> ### Ground rules
>
> 1. **Never weaken a claim to make a test pass.** No relaxed threshold, no
>    softened assertion, no deleted check. If a fix is hard, it is hard.
> 2. **Test first.** For every behavioural change: write the test, paste its
>    failure, make the change, paste the pass. A change with no failing test
>    before it is not finished.
> 3. **Do not touch what is proven.** The eight checks, raw-byte verification,
>    gap arithmetic, auth on `/api/register` and `/api/gap`, the throttle. Leave
>    them unless an item names them.
> 4. **Already done means say so.** If an item turns out to be complete, report
>    "already done" with the evidence. Do not re-implement it to have something
>    to show.
> 5. **One commit per item**, subject line `S<n>: <what changed>`. A phase that
>    cannot be committed cleanly has not finished.
> 6. **Regression is a stop.** If either suite goes red and you cannot fix it
>    within the same item, revert that item and report it. Never leave the tree
>    red at a gate.
> 7. **Out of scope:** anything needing a component, and any deck work beyond
>    the one retitle in Phase 3.

---

## PHASE 0 — baseline *(do not skip)*

Prove the tree is green before you change anything, so that any later red is
unambiguously yours.

```
make firmware-test          # expect: 92 checks, 0 failed
make demo                   # expect: DEMO PASSED
python -m pytest backend/tests -q   # expect: 124 passed
```

**Gate:** all three pass, output pasted, numbers recorded. If any is red on a
clean clone, **stop and report** — do not begin Phase 1.

---

## PHASE 1 — arrival-day documentation · S1

No code. This is first because eleven files already cite these documents and
neither exists, and because the day the parts land is the wrong day to write
them.

**`docs/HIL.md`** — seven numbered steps. The step numbers are fixed: the
UNPROVEN banners in `ac_lora.h`, `ac_nfc.h`, `ac_atecc.h`, `ac_net.h`,
`smoke.cpp`, `main.cpp` and `gateway.cpp` already point at them.

| Step | What | Passes when |
|---|---|---|
| 0 | `pio run -e smoke -t upload` | every probed part prints a line |
| 1 | `-e node_mock` + `bridge_serial.py COM<n>` | records reach the dashboard |
| 2 | real SHT40 (`-e node`) | temperature follows a hand on the sensor |
| 3 | PN532 tap | `T <device> assign <uid> <time>` on serial |
| 4 | ATECC608B | firmware version non-zero; signature verifies against its public key |
| 5 | SX1262 pair, node → gateway | a record crosses the radio byte-for-byte |
| 6 | gateway Wi-Fi + NTP | clock syncs, records reach the server |

Each step carries: the exact command, the expected output **written out in
full**, and the two or three specific ways it goes wrong — I2C addresses (SHT40
0x44, ATECC608B-TNGTLS 0x35, blank ATECC608 0x60, PN532 0x24), octal PSRAM
occupying GPIO33–37, PN532 DIP switches not set to I2C, the SX1262 pin map in
`ac_pins.h` **which has never been checked against the module's datasheet** —
say so in the document.

**`docs/BRINGUP.md`** — the irreversible steps and their warnings. Chiefly: a
blank ATECC608 needs its configuration zone written and **locked permanently**.
`ac_atecc.h` refuses to do this today. Document what it would take, and state
plainly that it must not happen by accident on two parts you cannot replace
before the finale.

Cross-check every `docs/HIL.md step N` reference in the source against the step
you actually wrote. A banner pointing at the wrong step is worse than none.

**Gate:** both files exist; every source reference resolves to a real step;
Phase 0's three commands still pass.

---

## PHASE 2 — the record format decision · S2

The largest remaining decision, and it is invisible right now.

`AteccSigner` implements **`IEcdsaSigner`, not `ISigner`**: ECDSA P-256 produces
**64 bytes**, and `ac_record.h:48` is `uint8_t sig[32]`. **Fitting the secure
element does not give you ECDSA.** A record format change does.

**Step 1 — decide, and say which.** If a human is available, put the choice to
them with the cost of each. If not, apply this rule: choose (b) only if Phases
3–5 can still be completed afterwards; otherwise (a).

**(a) Ship HMAC.** Write the defence into `backend/README.md` and a short
`docs/CRYPTO.md`: the ATECC is on the BOM, the interface exists, the migration
is specified, symmetric signing is a scope decision and not an oversight.
Name the consequence honestly — the buyer cannot verify a signature, only the
hash chain. Cost: an hour.

**(b) Record format v2.** `kRecBytes` 84 → 116, `sig[64]`, **and a
format-version byte**. Touches `ac_record.*`, `checks.py` (must parse **both**
versions — v1 records already in databases and captures must keep verifying),
`db.py` (raw bytes become length-tagged), `ac_node.cpp`, `ac_sim.*`,
`tools/dump.cpp`, `tools/fleet.cpp`, and `ac_lora.*` — LoRa frames are fixed at
`kRecBytes`, so **recompute the airtime budget** and say whether the duty cycle
still holds. Then re-green both suites.

**Step 2 — whichever you chose, add the version byte.** A format with no
version field can never be changed. If you chose (a), add the byte and nothing
else: it is cheap now and impossible later.

**Gate:** the decision is written down with its reasoning; if (b), both suites
green and a v1 capture still verifies; if (a), `docs/CRYPTO.md` exists and the
version byte is in.

---

## PHASE 3 — protect the demo · S3, S4, S5, S6, S7

In this order. S3 first because it is the highest value per hour in the project.

**S3 · stale-capture guard.** `feed_sim.py` has no age check; `MAX_HOLD_S =
30*86400` is in `checks.py:18`. Replay a three-day-old capture today and every
record is refused with nothing on screen saying why.

- read the first record's timestamp before posting anything
- take the window from the server — add a read-only field to `/api/state`; do
  **not** hard-code 30 days in a second place
- older than the window → exit non-zero, post nothing, print
  `regenerate: mingw32-make fleet` and the replay command
- older than a **quarter** of the window → warn on stderr and continue
- `--allow-stale` for archived captures, with a one-line warning when used

Tests: fresh feeds; stale exits non-zero and posts nothing; `--allow-stale`
feeds; 30%-of-window feeds with a warning.

**S4 · persist `AlertEngine.suspect`.** `alerts.py:32` is `self.suspect =
set()`; no table in `db.py`. Follow the existing convention for small engine
state rather than inventing a second one. Persist device, truck, time flagged
and the readings that caused it. `clear_suspect()` deletes the row — otherwise
a recovered node returns suspect after the next restart, the same bug pointing
the other way. `/api/reset` clears the table. `truck_state` run-lengths: persist
only if cheap, otherwise skip and say so.

Tests: flag, rebuild the engine against the same database, assert still suspect
and alerts still held; clear, rebuild, assert trusted and the row gone;
`/api/reset` leaves none.

**S5 · retitle the deck's AI/ML box.** Slide 3 has **"AI / ML Analytics"** over
anomaly detection, cause classification, shelf-life estimate, quality risk
score. All four exist; **none is ML** — `shelflife.py` is Q10 kinetics and
rules, deliberately, and says why in its docstring. Retitle to **"Analytics
Engine"**, subtitle *Q10 kinetics · rule-based classification*. Change nothing
else on the deck.

**S6 · one-command demo.** `make demo-full`: clean → build → capture → serve →
feed. Plus a `--seed` path building a known-good database from a committed
capture, as a fallback when live generation misbehaves on the venue laptop.

**S7 · make `check_deck.py` runnable.** Commit the deck to
`docs/SIH2026_26232_AnnaChain_OfficialFormat.pptx` (with S5 applied), then run
`python tools/check_deck.py` and paste the result.

**Gate:** both suites green; `make demo-full` runs end to end from a clean
clone; `check_deck.py` exits 0.

---

## PHASE 4 — close the honest gaps · S8, then S9 → S10, then S11, S12

S9 before S10: S10 depends on it.

**S8 · calibration API.** Check 7 is real — `checks.py:169` compares the reading
against `cal_due`, marks the device, raises an alert, and keeps the reading
rather than refusing it. But `db.set_calibration()` has **no endpoint calling
it**, so calibration can only be set by writing to SQLite directly. Add
`POST /api/calibration/{device}` under operator auth, and seed the demo devices
with a plausible EN 13486 interval. Test: set a lapsed date, feed a record,
assert the alert fires and the reading is stored and marked uncertified.

**S9 · gateway relays the server's last-ACK.** `ac_lora.cpp:151` returns the
gateway's own `ack_`. Have the gateway hold the server's last-ACK per device,
refreshed when it forwards or polls, and return that. **Decide explicitly what
to return when it has no server value yet** — its own `ack_` causes the bug,
zero makes the node resend its whole flash — and document the choice in the
header. Testable entirely in the simulator. Test: 40 records, 25 forwarded and
ACKed, node reboots, assert it resumes at 26; plus the no-value-yet case.

**S10 · gateway-side loss.** When the gateway's own buffer overruns, records are
counted there but nothing tells the server, and the node was ACKed hop-by-hop
so it does not resend. Prefer end-to-end reconciliation through S9. An
**unsigned** gateway counter is a trap — it looks like evidence and is not. If
you cannot close it, write the limitation into `ac_gateway.h` with the
reasoning and move on.

**S11 · NFC taps over LoRa.** `ac_record.h:73` defines only `FRAME_RECORD` and
`FRAME_GAP`. There is no `FRAME_TAP`, so on `env:node_lora` custody events are
not carried at all — they exist only over USB. Either add a tap frame, or state
in `backend/README.md` and on the deck that handover is the USB-tethered
configuration. Do not leave a slide implying it works over the radio.

**S12 · flag simulated ethylene.** `dump --ethylene` invents a curve and no flag
records that, so neither the server nor a later reader can tell. Add
`FLAG_SIMULATED` at bit 6 (0–5 are taken) and badge those readings in the
dashboard and the trace page.

**Gate:** both suites green; each item DONE, DECIDED (which way) or NOT DONE
(why).

---

## PHASE 5 — repo and evidence · S13, S14, S15

**S13 · CI.** No `.github/` exists. A workflow running `make test` on push and
pull request. Both suites already pass from a clean clone, so this should work
first try; if it does not, the difference between your machine and a clean
runner is itself worth knowing. Add the badge to `README.md`.

**S14 · commit the audit trail.** `docs/FIX.md`, `docs/READINESS.md`,
`docs/NOHW.md`, `docs/SOFTWARE_REMAINING.md`. Five real defects found and fixed
is part of the story, not an embarrassment.

**S15 · `LICENSE` and pinned requirements.** No LICENSE file.
`requirements.txt` is all `>=`, so a fresh install on the venue laptop may not
match what you tested. Add `requirements.lock` with exact versions; keep
`requirements.txt` readable.

**Gate:** CI green on a pushed commit; both suites still pass.

---

## Reporting

After **each phase**, short:

- per item: **DONE / ALREADY DONE / DECIDED (which way) / NOT DONE (why)**
- files touched, and the commit subject
- the failing test before and the passing test after
- the gate commands and their output

At the **end of all phases**, two things only:

1. **What is demonstrable now that was not at Phase 0**, one sentence each.
2. **Anything you found that is not in this brief.** That is the half worth
   reading.

Do not compute a completion percentage. Do not summarise the project's health.
Do not be encouraging. The team needs to know what will go wrong on stage.
