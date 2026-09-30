# What to order, and in what order

Buy in three waves. Wave 1 is enough to win Evaluation 1, and it is under ₹1,500.
Do not buy wave 3 until a mentor has told you which ethylene sensor to defend.

Prices checked on **29 September 2026**. Indian suppliers ship in 2–5 days;
confirm the price on the page before you pay, because these move.

---

## Wave 1 — order today (₹1,300 or so)

This is the whole Evaluation-1 demo. One board, one sensor, a cable.

| What | Why | Where | Price |
|---|---|---|---|
| **ESP32-S3-DevKitC-1 N16R8** | The node. 16 MB flash is what gives you the 90-day ring | [Robu](https://robu.in/product/esp32-s3-devkit-esp32-s3-wroom-1-n16r8/) | **₹839** |
| **GY-SHT40 module** (unwelded) | Temperature and humidity, ±0.2 °C, I²C | [Robu](https://robu.in/product/unwelded-gy-sht40-digital-temperature-and-humidity-sensor/) | **₹239** |
| USB-C data cable | Power, flashing, and the "network" for the demo | anywhere | ~₹150 |
| Breadboard + jumper wires | — | anywhere | ~₹150 |

**Buy two of the ESP32-S3 boards.** The cross-node self-diagnosis demo needs two
nodes disagreeing, and a spare board saves you the evening you brick one.

Wire it:

```
SHT40 VIN ── 3V3        SHT40 SDA ── GPIO 8
SHT40 GND ── GND        SHT40 SCL ── GPIO 9
```

That is the whole circuit. Pin numbers are at the top of `src/main.cpp`; change
them there if your module differs.

---

## Wave 2 — order once wave 1 works (₹2,000–3,000)

Only after the BOOT-button demo runs end to end.

| What | Why | Notes |
|---|---|---|
| **SX1262 LoRa module, 865–867 MHz** | The real radio. Must be **IN865**, not 433 or 915 — 865–867 MHz is the Indian ISM band | Two of them: one node, one gateway |
| **ATECC608B-TNGTLS** | The secure element. Turns the HMAC stand-in into real in-device ECDSA | Sold as a bare SOIC-8; get a breakout or an adapter |
| **Reed switch + magnet** | Tamper detection, the cheapest honest kind | GPIO 4 in the current pinout |
| **18650 cell + TP4056 charger + holder** | Runs it off the bench supply | See the cold-charge warning below |
| **6 V 1 W solar panel** | The energy-harvesting claim, made visible | Small is fine; you are proving the principle |
| **ADXL345 or LIS3DH** | Movement, and the "was it handled roughly" story | I²C, shares the SHT40 bus |

⚠️ **The cold-charge trap.** A Li-ion cell must not be charged below 0 °C —
it plates lithium and the cell is damaged for good. A plain TP4056 will happily
do it. For anything touching a frozen lane, use **LiFePO₄** with a matching
charger, or add a thermistor and inhibit charging below 0 °C. This is already on
slide 4 as a named risk; make the bench build match what the slide says.

---

## Wave 3 — the one decision you should not make alone

**The ethylene sensor is deliberately unfrozen**, and the deck says so. Three
options were costed; the difference between them is real money and real
credibility:

| Option | Part | What it honestly measures | Added cost |
|---|---|---|---|
| **A** | VOC proxy (BME688 / SGP40) | *Something changed in the headspace* — not ethylene, and you must say so | ~₹370 |
| **B** | MOS ethylene-sensitive element | Ethylene at ppm, with cross-sensitivity | ~₹545 |
| **C** | Electrochemical ethylene cell | Ethylene at low ppm, with a real calibration story | ~₹1,930 |

Below 1 ppm needs laboratory instruments. Nothing in this table gets you there,
and claiming otherwise is how you lose a room.

**Take this to Mentoring Round 1 and ask which one they would defend.** Then buy
that one and say why in Evaluation 2. Judges reward a decision that visibly
changed for a stated reason far more than one that was made in August.

---

## What you do not need to buy

- A development board with a display. You have a laptop.
- An enclosure, yet. For the water demo a sealed plastic container and a cable
  gland is enough, and it looks more honest than a bought IP67 box.
- A cellular modem. The gateway story works over Wi-Fi for the finale.

---

## Total

| Wave | Spend | Gets you |
|---|---|---|
| 1 | **~₹2,100** (two boards) | The Evaluation-1 demo, complete |
| 2 | ~₹3,000 | LoRa, the secure element, solar, battery, tamper |
| 3 | ₹370–₹1,930 | The ethylene decision, after a mentor weighs in |

Under ₹6,000 for a Hardware finale entry, which is itself part of the pitch: the
node BOM on slide 4 is ₹1,839 because every part in it is a catalogue component
you can buy from Robu on a Tuesday.
