<!-- The Phase 7 brief, as pasted into the build session (30 Sep – 1 Oct 2026). Reproduced as received. P1–P6 are on branch phase-7; P7 is on branch phase-7-p7. -->

# AnnaChain — Phase 7: the clock, and the rest of what breaks on stage

Phases 0–6 are on PR #1 and CI is green. This phase fixes what that run
*found* rather than what it was asked to build.

**Item 1 is the whole reason this phase exists.** Everything else can slip.

Baseline to hold: **113 firmware checks**, **DEMO PASSED**, **148 passed + 1
skipped** (Linux) / **152 passed** (Windows with Chrome).

---

## THE PROMPT

> You are fixing defects in **AnnaChain** found during the Phase 0–6 build
> (PR #1, branch `build-brief-phases`). Work on that branch or a branch from
> it — do not start from `master`, which is two weeks behind.
>
> Ground rules from the previous brief still apply in full: never weaken a
> claim to make a test pass; write the failing test first and paste both
> outputs; one commit per item; a regression is a stop, not something to push
> through; "already done" is a valid answer with evidence.
>
> Items are in priority order. **P1 is not optional and nothing else matters
> until it is done.**

---

## P1 · The node has no clock *(CRITICAL — breaks on a known date)*

**What happens.** `ArduinoClock::now()` returns `base_ + millis()/1000`, with
`base_` set once from `kClockBase = 1790294400` (25 Sep 2026) and **never
updated**. `ac_esp.h:24` has a `setBase()` method. Nothing in the codebase
calls it.

Three consequences, and all three are real:

1. **From 25 October 2026 every record from a real board is refused**, because
   `checks.py` refuses readings more than `MAX_HOLD_S` (30 days) old. That date
   is before the finale.
2. **Before that date, any node reset breaks the chain** — `millis()` restarts
   at zero, so the next timestamp is earlier than the last one and the record
   fails "timestamp went backwards".
3. **NTP on the gateway does not help**, because nodes stamp their own records
   and the gateway never tells them the time.

This is my code and my mistake. The mechanism was built and never wired.

**Do not fix this by relaxing `MAX_HOLD_S`.** It is a real security property —
it is what catches a node whose clock started a year wrong on its very first
record. The node's clock is what is broken.

### Four parts, all needed

**(a) Stop the compiled-in date ageing.** `kClockBase` is a constant that gets
staler every day the project sits. Derive it from build time instead
(`__DATE__`/`__TIME__`, or an `AC_CLOCK_BASE` build flag defaulting to the
build timestamp), so a freshly flashed board is never more than hours out
rather than months. This alone removes the October cliff. Do it first — it is
the smallest change with the largest effect.

**(b) Wire up a real time source.** Add a time query to `ILink` alongside
`queryLastAck` — or extend `queryLastAck`, which the node already calls on
every resync, to return the server's time too. Then:

- `/api/lastack/{device}` returns the server's `now` in its response
- `SerialLink` and `WifiHttpLink` pass it through
- `LoraNodeLink` gets it from the gateway, which already has NTP (`ac_net.h`)
- the node calls `clk.setBase()` with it, on first contact and periodically

**(c) Survive a reset without going backwards.** Persist the last issued
timestamp to NVS with each record (or every N records — state the interval and
why). On boot, `base_` starts from the larger of the build-time base and the
persisted value. A node that reboots then resumes forward, never behind.

**(d) Mark records taken before the clock was set — do not silently ship
them.** Records logged before first contact carry an uptime-relative timestamp,
are already signed, and cannot be re-stamped. So flag them.

`Flags` is a `uint8_t` and bits 0–6 are taken. **`1 << 7` is the last free
bit** — using it exhausts the flags byte, which is worth noting in
`docs/CRYPTO.md` as an argument for format v2 later. Add `FLAG_TIMEUNSET`,
have `checks.py` accept those records with the mark rather than refusing them,
and have the dashboard and trace page say *timestamp is relative to power-up,
not wall clock*.

Bound it: once the clock has been set, no further record may carry the flag.
A node that never reaches a server keeps logging — that is the point of the
project — but it never claims a wall-clock time it does not have.

### Tests

Firmware: a node that reboots issues a timestamp no earlier than its last;
a node given a server time adopts it; records before first contact carry
`FLAG_TIMEUNSET` and records after it do not.

Backend: a record with `FLAG_TIMEUNSET` older than `MAX_HOLD_S` is accepted
and marked; the same record **without** the flag is still refused; a flagged
record arriving after the device's clock has been set is refused as
inconsistent.

**Gate:** simulate a board flashed today and run forward past 30 days of
uptime. Every record still accepted. Paste it.

---

## P2 · A flat battery reads 100%

`ac_esp.cpp:145-147`:

```cpp
uint32_t mv = analogReadMilliVolts(battPin_) * 2;
int pct = (int)((mv - 3300) * 100 / (4200 - 3300));
```

`mv` is unsigned, so below 3.3 V `mv - 3300` wraps to roughly four billion, the
division stays huge, and the `pct < 0` clamp never fires because the
subtraction already happened in unsigned arithmetic. **The low-battery alert
can never fire**, and a dying node reports full charge right up to the moment
it stops.

```cpp
int pct = ((int)mv - 3300) * 100 / 900;
```

Test the boundaries: 3.0 V → 0, 3.3 V → 0, 3.75 V → ~50, 4.2 V → 100, 4.5 V →
100. Also check whether any alert rule depends on `batt`, and whether one
should exist if it does not.

---

## P3 · A suspect node stays suspect for ever

S4 persisted the suspect set correctly, but **nothing calls `clear_suspect()`**.
A node that recovers is never trusted again until the database is reset — which
is the S4 bug pointing the other way, and worse, because it is now durable.

Call it from the diagnosis path when a flagged node agrees with its peers for
`DISAGREE_RUNS` consecutive buckets. Test: flag a node, feed agreeing readings,
assert it clears, the row is gone, and the `sensor_agrees` alert fires.

---

## P4 · The demo and the real board share device id `26232001`

On a database that has seen a demo, a real board is refused and every record
fails "bad signature", because the demo enrolled a different key under that id.
This will happen at the venue, and "bad signature" is the single worst error
message for it to happen with.

Two changes:

- give the real board a distinct device id (document it in `docs/HIL.md` step 1)
- when ingest fails on a signature **and** the device was enrolled by a
  different key, say so: *"this device id is enrolled with a different key —
  run `make clean` or rotate the key"*, not "bad signature"

The second matters more. A wrong error message here costs you the demo slot.

---

## P5 · A bench node cries tamper on every record

`FLAG_TAMPER` fires unless GPIO4 is tied to ground, so a board on a desk with
nothing attached reports a broken seal on every reading. Either enable the
internal pull-up and invert, or require the jumper and put it in `docs/HIL.md`
step 1 with the reason. Whichever you choose, say it in `ac_pins.h` too.

---

## P6 · The small ones

- **`make PY=python`** — `make` calls `python3`, which on the user's laptop is
  Python 3.14 without Playwright. Detect it, or document `PY=python` at the top
  of `RUN_ON_WINDOWS.md`. This bites once and wastes twenty minutes.
- **Serial appears only on the UART socket**, not the one labelled USB. One
  line in `docs/HIL.md` step 0.
- **LoRa airtime is calculated, not measured** — about 0.66 s per record, plus
  a query and reply every sample since S9. Say **calculated** wherever that
  number appears, and add measuring it to `docs/HIL.md` step 5. Two radios plus
  S9's extra round trip may change the duty-cycle answer.
- **`docs/POWER.md`** — the 36 µA and 3.8-year figures are computed and have
  never been measured. Write the measurement procedure now (what to put the
  meter on, what to expect, what would falsify it) so it can be run the day the
  boards arrive. If the measurement never happens, the deck must say
  *calculated*.
- **Delete `.claude/worktrees/agent-…`** from the repository.

---

## P7 · The four items the previous phase could not finish

- **S5 and S7** — the deck was not available to the cloud run. S5 is one edit:
  slide 3's box titled **"AI / ML Analytics"** becomes **"Analytics Engine"**,
  subtitle *Q10 kinetics · rule-based classification*. Nothing on that slide is
  machine learning and `shelflife.py` says so in its own docstring. Then commit
  the deck to `docs/` and run `python tools/check_deck.py`.
- **S14** — commit `FIX.md`, `READINESS.md`, `NOHW.md`, `SOFTWARE_REMAINING.md`,
  `BUILD.md` and this file to `docs/`.
- **LICENSE** — MIT unless there is a reason otherwise.

---

## Report

Per item: **DONE / NOT DONE (why)**, files, commit subject, the failing test and
the passing test. Then the P1 gate output.

At the end, one question answered honestly: **is there anything else in this
codebase that is correct today and becomes wrong on a date?** P1 was that, and
nobody went looking for it — it surfaced by accident. Go looking.
