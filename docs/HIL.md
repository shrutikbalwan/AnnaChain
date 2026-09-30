# Hardware in the loop — arrival day, in order

Seven steps, numbered 0 to 6. The numbers are fixed: the UNPROVEN banners in
`lib/ac/ac_lora.h`, `ac_nfc.h`, `ac_atecc.h`, `ac_net.h`, `ac_hal.h`,
`src/smoke.cpp`, `src/main.cpp`, `src/gateway.cpp` and `platformio.ini` point at
them. Do not renumber.

**What is true before step 0.** Every environment in `platformio.ini`
(`native`, `node_mock`, `node`, `node_lora`, `gateway`, `smoke`) compiles — first
checked on 30 Sep 2026 with PlatformIO Core 6.2.0, espressif32 7.1.3, RadioLib
7.8.1, Adafruit PN532 1.3.4, ArduinoECCX08 1.4.1, Adafruit SHT4x 1.0.5. None has
run on a board. The SX1262, PN532, ATECC608B and Wi-Fi/NTP drivers have never met
their parts. Everything below is a procedure for finding out, not a description
of something that works. When a step passes, paste its real output under it,
with the date, and change nothing else.

Irreversible actions (locking an ATECC608, erasing NVS, burning eFuses) are not
in this file. They are in [`BRINGUP.md`](BRINGUP.md), which also has the smoke
test's output format, the SX1262 troubleshooting table and the pin-check
procedure.

| Step | What | Passes when |
|---|---|---|
| 0 | `pio run -e smoke -t upload` | every probed part prints a line |
| 1 | `-e node_mock` + `bridge_serial.py COM<n>` | records reach the dashboard |
| 2 | real SHT40 (`-e node`) | temperature follows a hand on the sensor |
| 3 | PN532 tap | `T <device> assign <uid> <time>` on serial |
| 4 | ATECC608B | chip answers with a non-zero identity; a signature verifies against its public key |
| 5 | SX1262 pair, node → gateway | a record crosses the radio byte for byte |
| 6 | gateway Wi-Fi + NTP | the gateway's clock syncs, records reach the server |

---

## Before any step

- **Which USB port.** The ESP32-S3-DevKitC-1 has two USB-C sockets, labelled
  **USB** and **UART**. The board definition this project uses
  (`esp32-s3-devkitc-1`) does not set `ARDUINO_USB_CDC_ON_BOOT`, so `Serial` is
  UART0 and comes out of the socket labelled **UART**. Upload works through
  either; the serial output only appears on UART. A monitor that shows nothing
  after a successful upload is, first, the wrong socket.
- **Close the monitor before anything else opens the port.** `pio device
  monitor`, `bridge_serial.py` and `tools/server.py` cannot share a COM port. The
  second one fails with *Access is denied* / *could not open port*.
- **Start from a clean server database for every step that talks to the
  server** (`mingw32-make clean`, or delete `backend/annachain.db*` and
  `backend/ledger.jsonl`). The laptop demos (`dump`, `fleet`, the seed
  capture) enrol devices `26232001`–`26232003` with published dev keys. The
  board is **`26232101`** (decimal **639836417**; `AC_DEVICE_ID` in
  `src/main.cpp`, `-DAC_DEVICE_ID=0x26232102` for a second board), so a demo
  and the board no longer collide. Until 1 Oct 2026 the board was `26232001`
  too: on a database that had seen a demo its enrolment was refused (409) and
  every record failed `bad signature`. A database left over from an earlier
  flash of the same board still does that (the new flash made a new key);
  the server now says so beside the reason (step 1, failure 3).
- The server must listen on the network for anything that is not on the
  laptop's own USB port: `python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000`.

---

## Step 0 — smoke test: does every part answer?

`src/smoke.cpp`. Wire everything you have (SHT40, ATECC608B breakout, PN532 in
I2C mode, SX1262 module, battery divider) to the pins in `lib/ac/ac_pins.h`.
Parts you have not got yet are fine: their line says FAIL, which is the point.

```
pio run -e smoke -t upload
pio device monitor -b 115200            # Windows: add -p COM<n>
```

**Expected output, in full** (angle brackets are values that differ per part;
everything else is literal). The format is specified in
[`BRINGUP.md`](BRINGUP.md#smoke-test-output-format).

```
AC-SMOKE v1 build <Mmm dd yyyy hh:mm:ss>
BOARD   OK    ESP32-S3 rev <n>, flash 16 MB, PSRAM 8 MB
FLASH   OK    LittleFS <n> KB, <n> KB used, write/read ok
I2C     OK    <n> device(s): 0x24 PN532 0x35 ATECC608B-TNGTLS 0x44 SHT40
SHT40   OK    serial 0x<8 hex>, <t> C, <rh> %RH
ATECC   OK    addr 0x35, serial 0123<14 hex>, locked, slot 0 ECDSA sign+verify ok
SX1262  OK    version "<16 characters containing SX126>", begin at 865.0625 MHz ok (RadioLib 0)
PN532   OK    IC PN532, firmware <maj>.<min>
BATT    OK    <mV> mV at the cell (GPIO5 x2)
AC-SMOKE RESULT 8/8 OK  (<ms> ms)
type r to run again
```

(The I2C scan may omit 0x35: an ATECC608 sleeps and ignores its address until
woken. The ATECC line wakes it, so that line is the one that counts.)

**Passes when** there are eight part lines (BOARD, FLASH, I2C, SHT40, ATECC,
SX1262, PN532, BATT) and a RESULT line — one line for every
probe, OK or FAIL, and the program did not hang or reset part-way. A FAIL on a
part that is not fitted yet is a pass for step 0. A missing line is not.

**How it goes wrong**

1. **It does not compile on the arrival-day laptop**, though it compiled on 30
   Sep 2026. The library versions are ranges (`^`) in `platformio.ini`: a newer
   RadioLib or ArduinoECCX08 can break the build. Pin the versions listed at the
   top of this file and try again before changing any code.
2. **`BOARD FAIL … PSRAM 0 MB (expected 16 MB / 8 MB for an N16R8)`**, or the
   board boot-loops. The N16R8's PSRAM is **octal** and occupies **GPIO33–37**.
   `platformio.ini` sets `board_build.arduino.memory_type = qio_opi` for that;
   if it was changed or dropped, PSRAM reads 0. And never wire anything to
   GPIO35, 36 or 37 even though they are on header J3: on this module they are
   the PSRAM bus.
3. **I2C finds nothing, or the wrong address.** SHT40 is **0x44** (the
   SHT40-**B** variant is 0x45; the smoke test will call that `?`). ATECC608B
   Trust&GO (TNGTLS) is **0x35**; a blank ATECC608 is **0x60** and will report
   `UNLOCKED` — stop and read BRINGUP.md before doing anything to it. PN532 is
   **0x24**, but only with the board's interface DIP switches (or jumpers) set to
   **I2C**; on most red PN532 boards that is switch 1 ON, switch 2 OFF, but read
   the silkscreen on yours. Nothing at all on the bus: SDA/SCL swapped (SDA is
   GPIO8, SCL is GPIO9), no pull-ups, or no 3V3.

---

## Step 1 — the node on USB, fake sensors: do records reach the dashboard?

`src/main.cpp` built as `node_mock`: SimSensors on the board, LittleFS ring,
`SerialLink` to `backend/bridge_serial.py`. No parts needed but the board.

```
mingw32-make clean
python -m uvicorn backend.app:app --port 8000            # terminal 1
pio run -e node_mock -t upload                           # terminal 2
python backend/bridge_serial.py COM<n>                    # terminal 2, after upload
```

Open http://127.0.0.1:8000 and sign in (`operator` / `annachain` on a fresh
database).

**Expected output of `bridge_serial.py`, in full** for the first two samples
(the node samples every 5 s; one `+  1` line and one `#` line per sample):

```
bridging COM<n> <-> http://127.0.0.1:8000

# AnnaChain node 26232101
# flash ring 4096 records, holding 0, last seq 0, server has 0
# sensors: MOCK (no parts needed)
# link: USB serial
registered 26232101, server has 0
# press BOOT to drop the link, press again to restore it
# type 'wipe' to clear the flash and start a fresh run
  +  1  ack 1
#     1    4.<nn> C  9<n>.<nn> %  batt 100  sent  waiting 0
  +  1  ack 2
#     2    4.<nn> C  <nn>.<nn> %  batt 100  sent  waiting 0
```

(The `K 639836417 <64 hex>` line the node prints is consumed by the bridge, not
echoed: it is what produces `registered 26232101`.)

**The tamper jumper, from step 2 on.** Fit a jumper wire from **GPIO4 to GND**
(header J1 pin 4 to any GND pin) before flashing `node` or `node_lora`. GPIO4 is
the tamper reed loop with its internal pull-up on: a shut lid pulls it LOW
(sealed); HIGH means the loop is open, and every record carries `FLAG_TAMPER`
and raises a *tamper* alert. A bare board has an open loop, so without the
jumper it reports a broken seal on every reading, which is correct: nothing is
holding it shut. The sense is not inverted to make the bench quiet, because an
inverted loop reads a cut or unplugged wire as sealed (fail-open), which
defeats the sensor (`lib/ac/ac_pins.h`). `node_mock` (this step) does not read
the pin, so it needs no jumper. Take the jumper off to demonstrate tamper.

**Passes when** the dashboard shows device `26232101` with a line near 4 °C
growing by one point every 5 s, status **live**. Then press BOOT: the node prints
`# LINK DOWN — still logging`, the dashboard goes **silent**; press it again and
the held records arrive in one batch (`+ <n>`), drawn late in orange.

**How it goes wrong**

1. **The first records show "relative to power-up, not wall clock"** on the
   dashboard. Expected, not a fault: they were taken before the bridge's first
   `A <seq> <time>` reached the node (see *The node's clock* below). Every
   record after that is wall-clock time. If *every* record is flagged, the
   bridge is older than the time field (`A <seq>` with no time): update
   `backend/bridge_serial.py`.
2. **`inconsistent: this device's clock has already been set`**: a board whose
   flash was wiped (or a second board with the same id) sent a flagged record to
   a database that has seen this device's wall-clock records. Clean the
   database, as for failure 3.
3. **`enrolment refused (409)`** then `refused: bad signature` on everything,
   with the bridge adding *device 26232101 is enrolled with a different key…
   run `make clean` … or rotate the key*, and the dashboard's refusal showing
   *this record does not verify under the key enrolled for device 26232101 …
   the typical symptom of a database that enrolled this id with a different
   key*. The database holds this id with another key: an earlier flash whose
   NVS key was erased, or a board built with a demo id. `mingw32-make clean`
   and restart the server, or have an admin rotate the key
   (`POST /api/register` with `rotate: true`). Check 2 still refuses the
   records until then; the hint only says why.
4. **Nothing prints at all**: wrong USB socket (use **UART**), or the monitor
   still holds the port.

### The node's clock (P1, 1 Oct 2026)

Until 1 Oct 2026 the node's clock was a hand-typed date (`kClockBase`, 25 Sep
2026) plus uptime, and nothing ever set it. Every record from a board that had
not been re-flashed was refused from 25 Oct 2026 (check 5, 30 days), and any
reset sent time backwards. What happens now, and why:

- **Where it starts.** `kClockBase` is the build time (`__DATE__`/`__TIME__`,
  minus 14 h because those are the build machine's local time with no zone),
  or `-DAC_CLOCK_BASE=<unix>`. Never ahead of true UTC, at most 26 h behind.
- **Where the time comes from.** The server's clock rides on the answer the
  node already asks for on every resync: `/api/lastack` returns `now`; the
  serial bridge answers `Q <dev>` with `A <seq> <now>` (a board that predates
  the field reads `<seq>` and ignores the rest); the gateway puts it in its
  `FRAME_LASTACK` answer (the server's time, or its own once NTP has synced).
  The node sets its clock from it every time it arrives, not only the first.
- **Before that, the flag.** Records taken before the first answer carry
  `FLAG_TIMEUNSET` (bit 7): their timestamp is uptime from `kClockBase`. The
  server accepts them without the 30-day age rule and the dashboard and buyer's
  page say *timestamp is relative to power-up, not wall clock*. Once the node
  has stamped one record with a set clock, it never flags again; once the
  server has accepted one unflagged record from a device, it refuses a flagged
  one from it as inconsistent, and remembers that across restarts
  (`devices.clock_set`).
- **A reboot never goes backwards.** At boot the node reads the newest record
  in its flash and resumes its clock from that record's timestamp, and no
  record is ever stamped earlier than the one before it (a server time slightly
  behind a fast crystal holds the clock still rather than stepping back).
  **Persistence interval: every record, at zero extra cost.** The timestamp is
  already written to flash in each record before the record can leave the node,
  in the same LittleFS write, and LittleFS commits a file write atomically. So
  the persisted value is exactly the last issued timestamp (margin 0: nothing
  that left the node can be later than the newest record in flash), and there
  is no second copy to wear flash or disagree. A separate NVS key would be a
  second write per sample (about 105,000 a year at 5 minutes, which NVS's wear
  levelling could take) to store a value the log already holds; it was not
  added.
- **A reboot after the clock was set, before the server is reached again**:
  the records are not flagged (the node has already claimed wall-clock time,
  and flagging again is what the server's consistency rule forbids), and their
  timestamps are a lower bound: late by however long the board was without
  power. The first record after power-up carries `FLAG_SELFTEST`, which marks
  where that starts; the next server answer corrects the clock, forward only.
- **Monotonic across the switch.** The server compares flagged timestamps with
  each other and wall-clock timestamps with each other. The first wall-clock
  record is not compared with the flagged ones before it: they are two
  different clocks, and the node takes the server's time as it finds it.
- **Not authenticated.** The time is not signed. Whatever carries it (the
  bridge, the gateway, the network) can shift it, bounded by the node never
  stepping backwards and by check 5 (60 s ahead, 30 days behind). A signed time
  from the server is future work (docs/CRYPTO.md).
- **Uptime** is read from `esp_timer` (64-bit), not `millis()`, which is 32-bit
  and wraps after 49.7 days of uptime.

UNPROVEN on a board, like the rest of this file: the simulated gate
(`backend/tests/test_clock_gate.py`, a board flashed today run for 36 days with
a reboot at 32.5) passes; step 1 above is where it is checked for real.

---

## Step 2 — the real SHT40: does the temperature follow a hand?

`node`: the same firmware with `Sht40Sensors` on I2C (SDA GPIO8, SCL GPIO9, 3V3,
GND). Same terminals as step 1.

```
mingw32-make clean
pio run -e node -t upload
python backend/bridge_serial.py COM<n>
```

**Expected output**, as step 1 except for the sensor line and the values:

```
bridging COM<n> <-> http://127.0.0.1:8000

# AnnaChain node 26232101
# flash ring 4096 records, holding <n>, last seq <n>, server has <n>
# sensors: SHT40 on I2C
# link: USB serial
registered 26232101, server has 0
# press BOOT to drop the link, press again to restore it
# type 'wipe' to clear the flash and start a fresh run
  +  1  ack 1
#     1   <room temperature, e.g. 26.84> C  <nn>.<nn> %  batt <n>  sent  waiting 0
```

Type `wipe` first if the board still holds step 1's records.

**Passes when** cupping a hand round the sensor raises the reading by at least
2 °C within three samples (15 s), and it falls back after you let go. The
dashboard line follows. Ice water in a sealed bag does the same thing downwards.

**How it goes wrong**

1. **`Sensor did not answer` alert on every record, temperature line empty.**
   The node could not read the SHT40 and flagged each record `FLAG_SENSORBAD`
   rather than invent a value. Wrong address (0x44 expected; step 0's I2C line
   tells you), SDA/SCL swapped, or the GY-SHT40 module unwelded and making
   intermittent contact.
2. **`Enclosure opened in transit` (tamper) alert on the first record.** The
   tamper input is GPIO4 with the internal pull-up, and it reads *open* unless the
   reed loop pulls it to GND. On a bench with no reed switch, tie GPIO4 to GND
   with a jumper, or every record carries `FLAG_TAMPER` (step 1, *The tamper
   jumper*, says why the sense is not inverted instead).
3. **Battery shows 0 %** with no divider on GPIO5: nothing is on the ADC, and
   0 % (with a *battery* alert) is the honest reading. Fit the 2:1 divider.
   Until 1 Oct 2026 this read 100 %: the conversion was unsigned and a cell
   below 3.3 V wrapped round to full. It is now `batteryPercent()` in
   `lib/ac/ac_batt.h`, tested in selftest (3.0 V reads 0 %, 3.75 V 50 %).

---

## Step 3 — the PN532: does a tap reach the serial line?

`node_lora` is the only environment built with `AC_NFC`. It also switches the
node's link to the SX1262, so without a working radio the node logs and holds
(that is fine for this step). Set the PN532's DIP switches to **I2C** first.

```
pio run -e node_lora -t upload
pio device monitor -b 115200
```

**Expected output, in full** — banner, then one line per tap:

```

# AnnaChain node 26232101
# flash ring 4096 records, holding <n>, last seq <n>, server has <n>
# sensors: SHT40 on I2C
# link: SX1262 LoRa <up|NOT READY> (UNPROVEN driver)
# NFC: PN532 ready (UNPROVEN driver)
K 639836417 <64 hex>
# press BOOT to drop the link, press again to restore it
# type 'wipe' to clear the flash and start a fresh run
T 639836417 assign <uid hex> <unix time>
T 639836417 tap <uid hex> <unix time>
```

(If no SX1262 is fitted there is also a `# SX1262 begin failed, RadioLib code
<n> — still logging` line before the banner.) A 4-byte MIFARE Classic UID is 8
hex characters; a 7-byte NTAG UID is 14.

**Passes when** the first tap after a fresh start (or after `wipe`) prints
`assign`, every later tap prints `tap`, and holding the same tag on the reader
prints it once, not once per poll.

**How it goes wrong**

1. **`# NFC: PN532 NOT FOUND`.** DIP switches not on I2C (the most common one),
   or IRQ/RST not on GPIO6/GPIO7 — the Adafruit driver waits on them even in I2C
   mode.
2. **The tag is never seen.** The PN532 finds only ISO 14443A tags
   (MIFARE/NTAG). A 125 kHz access-card fob is not one.
3. **The tap does not reach the server.** It only can over USB: close the
   monitor and run `python backend/bridge_serial.py COM<node>` on the **node's**
   port (with the server running). The bridge prints `tap assign <uid> ->
   commissioning` and the shipment gains a checkpoint. A tap is not carried over
   LoRa (there is no `FRAME_TAP`; see `backend/README.md`), and it is not signed
   or chained. The tap's time is the node's clock (step 1, failure 1).

---

## Step 4 — the ATECC608B: is it a real, locked P-256 signer?

The smoke test's ATECC probe (`AteccSigner` in `lib/ac/ac_atecc.*`) is the
procedure: it finds the chip, reads its serial number and lock state, reads
slot 0's public key, signs a digest in the chip, and checks that signature in
software with mbedTLS — so a pass means a standard ECDSA P-256 signature, not
merely one the chip agrees with. It never writes to the chip.

```
pio run -e smoke -t upload
pio device monitor -b 115200
```

**Expected output** — the ATECC line of step 0, in full:

```
ATECC   OK    addr 0x35, serial 0123<14 hex>, locked, slot 0 ECDSA sign+verify ok
```

**Passes when** that line says OK. The serial number standing in for "a non-zero
identity": every ATECC608 serial number starts `0123` (Microchip fixes the first
two bytes), so `0123` followed by fourteen hex characters that are not all zero
is the chip answering. The smoke test does not print the chip's revision
register; if you want it, that is a one-line addition, not something to assume.

This step proves the chip. It does **not** make records ECDSA-signed: records
still carry a 32-byte HMAC, and an ECDSA signature is 64 bytes. That is a record
format change, decided against for now and specified in [`CRYPTO.md`](CRYPTO.md).

**How it goes wrong**

1. **`ATECC FAIL no ATECC608 answered at 0x35 or 0x60`.** Bus wiring (step 0), or
   the SOIC-8 is soldered to its adapter rotated. Check pin 1.
2. **`ATECC FAIL addr 0x60, … UNLOCKED: chip is unlocked (blank part)`.** You
   have a blank ATECC608, not the Trust&GO part on the buy list. **Stop.**
   Provisioning it is irreversible; read [`BRINGUP.md`](BRINGUP.md) first.
3. **`sign: Sign failed` or `verify FAILED`.** A Trust&GO part whose slot 0 is
   not usable the way this driver assumes, or ArduinoECCX08 not talking to it
   correctly at 0x35 (the library's examples assume 0x60). Record the output; do
   not "fix" it by writing configuration.

---

## Step 5 — two SX1262s: does a record cross the radio byte for byte?

Two boards, both with an SX1262 and an antenna **fitted before power-up**. One
runs `node_lora` (the crate), one runs `gateway` (the cab) with no Wi-Fi
settings, so its uplink is USB serial to `bridge_serial.py`.

**The pin map is unchecked for your module.** `lib/ac/ac_pins.h` was checked
against the ESP32-S3-DevKitC-1 v1.1 user guide and the ESP32-S3 datasheet v2.2.
It has **never been checked against the SX1262 module's own datasheet**, because
the exact module has not been chosen. Before powering it, match every one of
NSS=GPIO10, MOSI=GPIO11, SCK=GPIO12, MISO=GPIO13, DIO1=GPIO14, NRST=GPIO17,
BUSY=GPIO18 to the module's pinout, and confirm its TCXO voltage and whether DIO2
drives its RF switch (BRINGUP.md, SX1262 table).

```
mingw32-make clean
python -m uvicorn backend.app:app --port 8000
pio run -e gateway -t upload --upload-port COM<gateway>
pio run -e node_lora -t upload --upload-port COM<node>
pio device monitor -p COM<node> -b 115200         # copy the K line, then close it
python backend/bridge_serial.py COM<gateway>
```

The node's `K <device> <key>` line appears on the **node's** serial port, which
the bridge is not reading, so enrol the node by hand once (operator login):

```
python -c "import json,urllib.request as u;B='http://127.0.0.1:8000';p=lambda path,b,h={}:json.load(u.urlopen(u.Request(B+path,json.dumps(b).encode(),{'Content-Type':'application/json',**h})));t=p('/api/login',{'username':'operator','password':'annachain'})['token'];print(p('/api/register',{'device':639836417,'key_hex':'<64 hex from the K line>'},{'Authorization':'Bearer '+t}))"
```

**Expected output** of the bridge on the gateway's port:

```
bridging COM<gateway> <-> http://127.0.0.1:8000

# AnnaChain gateway AA000001
# uplink: USB serial  ·  clock: build time + uptime (not synced)
# LoRa: SX1262 up (UNPROVEN driver)
# buffer 4000 frames · holding 0 · dropped 0 records, 0 gap notices
# gap notices: heard 0 · forwarded 0
# heard 0 records from 0 nodes · forwarded 0 · duplicates 0
# press BOOT to drop the uplink, press again to restore it
  +  1  ack 1
# forwarded 1 · holding 0
```

**Passes when** the server accepts the record (`+  1  ack 1`). The server
accepts a record only if its signature verifies over all 84 bytes as the node
signed them, so an accepted record is one that crossed node → radio → gateway →
USB unchanged. Then type `stat` into a monitor on the gateway (after closing the
bridge) and check `heard <n> records from 1 nodes`.

Every sample, before sending, the node also asks the gateway what the server
holds (a `FRAME_QUERY`, answered with `FRAME_LASTACK`; `lib/ac/ac_gateway.h`).
With the USB uplink the gateway answers it by asking `bridge_serial.py` (a `Q`
line), so with the bridge stopped the node gets "no value" and carries on from
what it last knew — that is the designed behaviour, not a fault.

**Before this counts, time it on air.** By the Semtech time-on-air formula a
record frame (86-byte payload: magic, kind, 84-byte record) at SF9, 125 kHz,
CR 4/7, 8-symbol preamble, explicit header, CRC on is about **0.66 s**, and the
gateway's 10-byte ACK about 0.17 s. That is arithmetic, not a measurement; check
it against the power and duty-cycle limits you are working to before a long
run.

**How it goes wrong**

1. **`SX1262 begin failed, RadioLib code -707`** (SPI command timeout): almost
   always the TCXO. Rebuild with `-DAC_LORA_TCXO_V=0` for a crystal module, or
   the module's actual TCXO voltage. **-2** (chip not found): NSS/SCK/MISO/MOSI,
   or BUSY stuck high.
2. **The node transmits, the gateway hears nothing.** Frequency or sync word
   differ (both sides must be the same build of this repo), no antenna on one
   side, or DIO2 not driving the RF switch on this module
   (`-DAC_LORA_DIO2_RFSW=0/1`).
3. **Records refused `unknown device`**: the node was not enrolled (the K line
   above). **`bad signature`**: a different key was enrolled — or the record
   really was altered in transit, which is exactly what the check is for.

---

## Step 6 — the gateway on Wi-Fi with NTP: does its clock sync, do records arrive?

The gateway's uplink becomes `WifiHttpLink` (HTTP to the API) and its clock
`NtpClock`, both in `lib/ac/ac_net.*`. Settings come from the build environment,
never from source.

PowerShell:

```
$env:AC_WIFI_SSID="<2.4 GHz network>"; $env:AC_WIFI_PASS="<password>"
$env:AC_SERVER_URL="http://<laptop IPv4 from ipconfig>:8000"
pio run -e gateway -t upload --upload-port COM<gateway>
python -m uvicorn backend.app:app --host 0.0.0.0 --port 8000
pio device monitor -p COM<gateway> -b 115200
```

**Expected output on the gateway's monitor, in full** (the node from step 5
running and enrolled):

```
# Wi-Fi up, <gateway IPv4>

# AnnaChain gateway AA000001
# uplink: Wi-Fi HTTP (UNPROVEN)  ·  clock: NTP
# LoRa: SX1262 up (UNPROVEN driver)
# buffer 4000 frames · holding 0 · dropped 0 records, 0 gap notices
# gap notices: heard 0 · forwarded 0
# heard 0 records from 0 nodes · forwarded 0 · duplicates 0
# press BOOT to drop the uplink, press again to restore it
# forwarded 1 · holding 0
```

**Passes when** the banner says `clock: NTP` and the dashboard shows the node's
records arriving with no USB cable on the gateway's data port.

Records are stamped by the **node**. The gateway passes the time down in its
answer to each node's query: the server's time when the server gave one, else
its own NTP time (step 1, *The node's clock*). A node records the time it was
given; it never stamps a record earlier than the one before.

**How it goes wrong**

1. **`# Wi-Fi did not connect`.** The ESP32-S3 is 2.4 GHz only: a phone hotspot
   on 5 GHz is invisible to it. Or the variables were not set in the shell that
   ran `pio run`; then the banner says `uplink: USB serial`, because an empty
   SSID falls back to the serial link.
2. **Wi-Fi up, nothing reaches the server.** uvicorn bound to 127.0.0.1 (use
   `--host 0.0.0.0`), Windows Firewall blocking inbound port 8000 on a Public
   network profile, or `AC_SERVER_URL` pointing at 127.0.0.1 (that is the
   gateway itself).
3. **`clock: build time + uptime (not synced)`**, with `# NTP did not answer`. The
   network blocks UDP 123 (common on venue and campus Wi-Fi). The gateway retries
   every 30 s; a phone hotspot usually allows it.
