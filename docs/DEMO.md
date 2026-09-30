# The ninety seconds

Judges see a lot of dashboards. They see very few devices that survive something
happening to them. This is the script.

---

## Setup, before they arrive

**Start from a clean database, every time.** A database left over from testing
carries whatever was done to it — re-keyed devices, declared gaps, extra test
nodes — and the dashboard will show all of it to the judges.

```
make clean                  (or delete backend/annachain.db* and backend/ledger.jsonl)
```

One command does all of it: `mingw32-make demo-full` (clean, build, capture,
serve, feed). If the compiler will not cooperate at the venue,
`mingw32-make demo-seed` builds the same known-good database from a committed
capture, re-timed to end now (see the Makefile for why that is honest).

By hand: make the capture right before replaying it. The trip
ends at the moment it is captured, and the server refuses readings older than a
node could have held them. Leave out `--ethylene`: the board has no ethylene
sensor, and the dashboard should show what the device actually does.

```
make fleet
python3 backend/feed_sim.py fleet.capture --base http://127.0.0.1:8000
```

Two terminals, one board, one glass of iced water.

```
Terminal 1:  python3 tools/server.py /dev/ttyACM0
Terminal 2:  pio device monitor        (optional — the server echoes # lines)
```

Type `wipe` into the node once so the run starts at sequence 1. A judge who
watches the counter start from 1 believes the counter.

---

## The performance

**0:00 — "Every reading is signed and saved on the device before anything is sent."**

Point at the server window. Records arriving, one every five seconds, sequence
numbers climbing. Let it reach about 40. Do not narrate the obvious.

**0:20 — "Now the truck goes through a dead zone."**

Press **BOOT**.

```
# LINK DOWN — still logging  (stored 41, waiting 0)
#    42   4.11 C  90.32 %  batt 100  HELD  waiting 1
#    43   4.07 C  90.14 %  batt 100  HELD  waiting 2
```

The server window goes silent. The node window does not. **Say nothing for ten
seconds and let them watch the two windows disagree.** That silence is the whole
problem statement.

**0:45 — "Twenty records exist now that the server has never seen."**

Let `waiting` reach 20 or so.

**0:55 — "The signal comes back."**

Press **BOOT** again.

```
# LINK UP — filling the gap  (stored 61, waiting 20)
```

Twenty records land in the server window at once, in sequence, all eight checks
passing. Point at the sequence numbers: **no gap**.

**1:10 — "Now let me break it."**

Stop the node. Open `records.jsonl`, change one temperature, and feed it back:

```bash
python3 tools/server.py --replay tampered.jsonl
```

```
REJECTED  seq 47: bad signature
REJECTED  seq 48: broken hash chain
```

**"One edited reading, and everything after it stops verifying. That is what the
blockchain is for — not to store the data, to make the record unchangeable
after the fact."**

**1:25 — the water.**

Sealed box, sensor inside, spray bottle. It keeps logging. This takes four
seconds and it is the thing they remember at dinner.

---

## What to say when they push

**"Why not just use a cloud logger?"**
Because it signs in the cloud. The ledger then preserves whatever a server was
told, which is not the same as what happened to the food. We sign inside the
device, before the radio exists as far as the record is concerned.

**"Is that a real signature?"**
Not yet — it is HMAC, and the laptop needs the node's key to check it. That is
precisely why the ATECC608B is in the BOM: with ECDSA the private key never
leaves the chip and nobody else ever needs it. The interface in the code is
already the secure element's; only the implementation changes.

**"What if the outage is longer than the flash?"**
Then we lose the oldest records — and the node *declares exactly which ones*, in
a message the server records. A hole we can name is auditable. A hole nobody
mentions is the thing this whole project exists to prevent. There is a test for
it: `A very long outage — longer than the flash`.

**"What is your ethylene accuracy?"**
We have not chosen the sensor. Three options are costed on slide 4. Below 1 ppm
needs laboratory instruments, so we are not going to claim it. Which would you
defend?

---

## Across the three evaluation rounds

The finale gives you three evaluations with mentoring between them. Judges
reward movement. Plan to arrive unfinished.

| Round | Show |
|---|---|
| **Eval 1** | This script. Say openly that the ethylene sensor is unchosen and the signer is HMAC. |
| **Mentoring** | Ask which ethylene option they would defend. Write down the answer. |
| **Eval 2** | The sensor decided, with the reason. The ATECC608B fitted, signatures now ECDSA. Spray the box in front of them. |
| **Eval 3** | Two nodes over LoRa through the gateway, one node given a deliberate bias, cross-node self-diagnosis flagging it as a faulty sensor rather than reporting spoilage. |

---

## The photographs to take tonight

The deck has no prototype evidence. It is the single biggest gap in it.

1. The board on the bench, sensor attached, with the serial window behind it.
2. A screenshot of the server accepting the 20-record gap fill, sequence numbers legible.
3. A screenshot of `REJECTED — bad signature`.
4. The sealed box with water on it, still logging.

Four images. One goes on slide 4 where the prototype list is; the rest go in
your pocket for the mentoring rounds.
