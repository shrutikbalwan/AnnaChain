# Power: what 36 µA and 3.8 years rest on, and how to measure them

**Status (1 Oct 2026): CALCULATED, never measured.** The deck's figures (a node
idles at **36 µA** and runs for **3.8 years**) come from a slide whose inputs
are not in this repository. Nothing here has been put on a meter: no board has
arrived. If the measurement below is never run, the deck must say
*calculated*, next to both numbers.

Two facts from the code that the deck's figures depend on:

- **The node firmware never sleeps today.** `src/main.cpp` runs `loop()`
  continuously and samples every 5 s on the bench (`kSampleMs`). A 36 µA idle
  assumes deep sleep between samples, which is not written. Until it is, the
  node draws tens of mA, and the 3.8-year figure describes firmware that does
  not exist yet.
- **The radio time per sample grew in S9.** Every sample the node now sends a
  query and waits for the gateway's last-ACK before sending the record, then
  waits for the ACK. `tools/airtime.py` (CALCULATED by the Semtech formula):
  query 140 ms + record 656 ms = **0.80 s on air per sample** from the node, and
  it listens through the gateway's 198 ms reply and 169 ms ACK, plus turnaround,
  and up to 3 tries of each.

## The model, with the deck's inputs as named inputs

Mean current over one sample period `T`:

```
I_avg = [ I_sleep·(T − t_active − t_tx − t_rx)
        + I_active·t_active + I_tx·t_tx + I_rx·t_rx ] / T

life  = C · usable / (I_avg + self-discharge) / 8766 h
```

`tools/power.py` computes exactly this. It has **no defaults for the deck's
values**: every one must be given. Fill this table from the slide, then run it.

| Input | `tools/power.py` flag | Deck value (team to fill in) | Where it should come from |
|---|---|---|---|
| Cell capacity | `--cell-mah` | | the cell's datasheet |
| Usable fraction before brown-out | `--usable` | | cell discharge curve vs the regulator's dropout |
| Sample interval | `--sample-s` | | 300 in the field (`kSampleMs` comment) |
| Whole-node sleep current | `--sleep-ua` | 36 (deck) | measured, below |
| Awake current (sensor, sign, flash) | `--active-ma` | | measured |
| Awake time per sample | `--active-s` | | measured |
| SX1262 transmit current at 14 dBm | `--tx-ma` | | SX1262 datasheet, then measured |
| Time on air per sample | `--tx-s` | | default 0.80 s from `tools/airtime.py` (calculated) |
| Receive current | `--rx-ma` | | SX1262 datasheet, then measured |
| Receive window per sample | `--rx-s` | | measured (it includes waiting for the gateway) |
| Self-discharge | `--self-discharge-pct-yr` | | cell datasheet |
| Solar input | not modelled | | the deck's claim must say whether it counts it |

```
python tools/power.py --cell-mah <C> --usable <f> --sample-s 300 --sleep-ua 36 \
    --active-ma <I> --active-s <t> --tx-ma <I> --rx-ma <I> --rx-s <t>
```

**How 3.8 years follows, and the quick check.** 3.8 years is 33,311 hours, so
the claim needs a *mean* current, everything included, of
`C · usable / 33,311 h`: 30 µA per 1000 mAh of usable capacity
(`python tools/power.py --implied 3.8 --cell-mah <C> --usable <f>`). The
**idle current alone** at 36 µA already needs about **1,200 mAh usable** before
a single sample or transmission is counted. So the slide's cell must be well
above 1,200 mAh usable, and the sampling and radio must add little on top of
36 µA. If the slide's cell is smaller, 3.8 years does not follow from 36 µA,
and the slide is wrong whatever is measured.

For scale, the radio alone: 0.80 s on air per 5-minute sample is 0.27 % of the
time. At a transmit current of `I_tx` mA that adds `I_tx × 2.7` µA to the mean
(for example, a transmit current in the region of 100 mA would add about
270 µA, several times the 36 µA idle). Check `I_tx` against the SX1262 module
you actually have at 14 dBm (`AC_LORA_DBM`); this is the input most likely to
decide the answer.

## What would falsify it

Any one of these, measured on the hardware below:

1. **Sleep current above 36 µA** between samples, with the radio, sensor and
   secure element in their lowest states.
2. **Charge per sample** (the area under the current trace for one wake,
   sample, sign, write, query, send and ACK) larger than the model's
   `I_active·t_active + I_tx·t_tx + I_rx·t_rx` from the slide's values. A retry
   (no ACK) triples the radio part: count them in a real run.
3. **`tools/power.py` with the measured values** giving less than 3.8 years
   for the slide's cell.
4. **The slide's cell** being below ~1,200 mAh usable (see above).

## Where to put the meter, and on what

**A DevKitC measurement cannot confirm the claim.** The ESP32-S3-DevKitC-1 is a
development board: its 3.3 V LDO, its USB-UART bridge, its power LED and the
RGB LED are all on the same supply, and between them they draw far more than
tens of µA whatever the ESP32-S3 itself is doing. A DevKitC powered through
USB or its 5 V pin will read milliamps in deep sleep. That number says nothing
about 36 µA, and must not be quoted as a failure or a success of the claim.

What can be measured on a DevKitC, usefully:

- **The chip's own deep-sleep current**, by powering the module on its 3V3 pin
  directly from a bench supply through the meter, with the LDO bypassed (no
  USB, nothing on the 5 V pin), and the power LED removed or its resistor cut.
  The USB-UART bridge will still be powered from 3V3 on this board unless it is
  isolated, so read the result as an upper bound on the module. Espressif's
  figure to compare with: **7 µA** (RTC memory on, RTC peripherals off) or
  **8 µA** (RTC memory and peripherals on) in Deep-sleep, **240 µA** in
  Light-sleep (ESP32-S3 Series Datasheet **v2.2**, Table 5-10 *Current
  Consumption in Low-Power Modes*, p. 68). Octal PSRAM adds to Light-sleep
  (the table's note gives 140 µA for 8 MB octal PSRAM at 3.3 V).
- **The active part of a sample** (wake to sleep): its shape and duration are
  the same on any board, so `t_active`, `t_tx` and `t_rx` can be measured on a
  DevKitC even though the baseline cannot.

What the 36 µA claim needs:

- **The node as it will ship**: the ESP32-S3 module (or bare chip) on the
  project's own board or a low-quiescent carrier, with a low-Iq regulator (or
  none, from a LiFePO4 cell), no USB bridge and no LEDs; the SHT40, the SX1262
  module, the ATECC608B and the PN532 all fitted, each in its sleep state; the
  battery divider fitted (it is a constant drain: size it, and include it).
- **Firmware that sleeps**: deep sleep between samples, with the SX1262 put to
  sleep first. Not written yet (see the top of this file).
- **The meter**: in series with the cell's positive lead, the whole node after
  it, nothing else on that cell. A µA range is not enough on its own: a sample
  swings from µA to around 100 mA in milliseconds, which a multimeter's burden
  voltage and auto-ranging get wrong. Use a power profiler with a dynamic range
  from under 1 µA to over 100 mA and at least ~10 kHz sampling (for example a
  Nordic Power Profiler Kit II, a Joulescope, or an Otii), and record at least
  30 minutes: several samples, and the sleep in between.

**What to expect, if the claim is right:** a flat floor at or below 36 µA
between samples, one burst every sample interval, and the mean over a whole
number of intervals, put into `tools/power.py` with the slide's cell, giving
3.8 years or more.

**Write down** the date, the board, the firmware commit, the cell, the meter,
the floor current, the mean over N intervals, the retries seen, and the
`tools/power.py` line with the result, here, under this heading, and put the
deck's word (*measured* or *calculated*) next to its numbers accordingly.
