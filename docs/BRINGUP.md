# Bring-up — the things you cannot undo, and how to check the wiring first

[`HIL.md`](HIL.md) is the arrival-day order, steps 0 to 6. This file is what it
leans on: the smoke test's output format, the pin check, the SX1262
troubleshooting table, and — first, because it matters most — the actions that
cannot be taken back.

Nothing here has been run on hardware. No part has arrived.

---

## Irreversible, or expensive to undo

Read this before touching a part you cannot replace before the finale.

### 1. Locking a blank ATECC608 — permanent

**The rule: it does not happen by accident, and it does not happen at all to
the parts meant for the finale.**

The buy list specifies the **ATECC608B-TNGTLS** (Trust&GO). Per its data sheet
(Microchip DS40002250B, §2.1–2.2) it arrives with its Configuration zone
already locked at the factory, a P-256 private key generated inside the chip in
slot 0 that can never be read out, at I2C address **0x35**. Nothing needs to be
written to it, and `lib/ac/ac_atecc.*` never writes to the chip.

A **blank ATECC608** (any other ordering code; address **0x60**) is different. It
arrives unlocked. Before it can hold a key it needs:

1. its Configuration zone written — 128 bytes that decide, per slot, whether a
   key is private, whether it can ever be read, which commands may use it;
2. the Configuration zone **locked** — permanent, no command undoes it;
3. a private key generated in a slot;
4. the Data and OTP zones **locked** — permanent.

With ArduinoECCX08 that is, in outline, `ECCX08.writeConfiguration(...)`,
`ECCX08.lockConfiguration()`, `ECCX08.generatePrivateKey(0, pub)`,
`ECCX08.lockDataAndOTP()` — the sequence in the library's own `ECCX08CSR`
example. A wrong configuration locked in can leave the private key readable
(so the chip proves nothing) or the slot unusable for signing (so the chip is
scrap). Either way the part is finished.

`AteccSigner::begin()` **refuses** an unlocked chip and reports it as
unprovisioned (`chip is unlocked (blank part): it must be provisioned first`).
It contains no code that writes configuration or locks anything, and none should
be added to any environment in `platformio.ini`: every one of those runs at
boot, and a lock that runs at boot runs on whatever chip happens to be fitted.

If a blank part is all you have, what it would take:

- a **separate one-off sketch**, not in this repository's build environments,
  that prints the configuration it is about to write and **waits for a typed
  confirmation** (the chip's serial number, typed back) before each lock;
- the Configuration zone bytes for slot 0 checked against the ATECC608 data
  sheet by two people before the first lock: private key, never readable, ECDSA
  Sign enabled;
- run first on a **third, sacrificial part**, with the result recorded (smoke
  test ATECC line OK, signature verifies in software), and only then — if at
  all — on another;
- never on either of the two parts intended for the finale. If those are blank,
  the honest position is "the secure element is not fitted", not a lock done at
  midnight.

### 2. Erasing the board's flash — changes the device's identity

`pio run -t erase`, `esptool.py erase_flash`, or a partition-table change wipes
NVS, and NVS holds the node's signing key (`EspSoftSigner`, key `k` in namespace
`ac`). The next boot generates a **new** key. The server still has the old one:
the board's enrolment is refused (409) and everything it sends is `bad
signature` until an admin rotates the key (`rotate=true`, audited) or the
database is cleared. Records signed with the old key stay verifiable. Uploading
firmware normally (`-t upload`) does not erase NVS.

The same erase also wipes the LittleFS ring. Any record not yet acknowledged by
the server is gone, and nothing declares it lost.

### 3. eFuses — never

Nothing in this project needs an eFuse. Do not run `espefuse.py`, and do not
enable flash encryption or secure boot from menuconfig or an IDF example. Both
burn eFuses; both are permanent, and a mistake bricks the module.

### 4. Transmitting without an antenna

Fit the antenna to both SX1262 modules **before power-up**. The node transmits
on its first sample and the gateway transmits every ACK. Keying a power
amplifier into an open connector can damage it, and you have two modules.

### 5. The cell

Li-ion must not be charged below 0 °C (a plain TP4056 will, and the cell is
damaged for good — see `docs/BUY_LIST.md`). Reverse polarity on an 18650 holder
kills the TP4056 and possibly the board. The battery sense divider goes to
GPIO5 only; the cell never goes directly to any GPIO or to 3V3.

---

## Smoke test output format

`src/smoke.cpp` (HIL step 0). One header line, one line per part, one verdict:

```
AC-SMOKE v1 build <__DATE__> <__TIME__>
<PART>   <OK|FAIL|SKIP>  <detail>
...
AC-SMOKE RESULT <ok>/<total> OK[  FAIL: <part>, <part>]  (<ms> ms)
type r to run again
```

- `<PART>` is left-aligned in 7 columns, the status in 5, then the detail.
- Parts, always in this order: `BOARD`, `FLASH`, `I2C`, `SHT40`, `ATECC`,
  `SX1262`, `PN532`, `BATT`. Eight lines, OK or FAIL, every run.
- The verdict counts OK lines; `FAIL:` lists the parts that failed.
- Type `r` and Enter to run it again, after re-seating a wire.

What each FAIL detail means is in HIL.md step 0 and in the table below.

---

## Checking the pin map on the bench

`lib/ac/ac_pins.h` is checked against the ESP32-S3-DevKitC-1 v1.1 user guide and
the ESP32-S3 datasheet v2.2, both cited in the file. It is **not** checked
against a board, and the SX1262 lines are **not** checked against the SX1262
module's own datasheet, because the module has not been chosen. The procedure:

1. **Before power.** With a multimeter in continuity mode, check each wire from
   the header pin to the part's pin, against the table below, and check that
   nothing is on GPIO0, 3, 19, 20, 26–37, 38–48 or 43/44. In particular nothing on
   **GPIO35, 36, 37**: they are on header J3 but are the octal PSRAM bus on the
   N16R8.
2. **Power the board alone** and run the smoke test (HIL step 0): BOARD must say
   16 MB / 8 MB.
3. **Add one part at a time** and re-run with `r`. A part that breaks the lines
   of parts already passing is shorting a shared line (SDA/SCL, 3V3).

| Signal | GPIO | Header (DevKitC-1 v1.1) | Part pin |
|---|---|---|---|
| I2C SDA (SHT40, ATECC608B, PN532) | 8 | J1-12 | SDA |
| I2C SCL | 9 | J1-15 | SCL |
| Tamper reed loop (to GND when shut) | 4 | J1-4 | reed switch |
| Battery divider (2:1) | 5 | J1-5 | divider midpoint |
| PN532 IRQ | 6 | J1-6 | IRQ |
| PN532 RSTO/RST | 7 | J1-7 | RST |
| SX1262 NSS | 10 | J1-16 | NSS / CS |
| SX1262 MOSI | 11 | J1-17 | MOSI |
| SX1262 SCK | 12 | J1-18 | SCK |
| SX1262 MISO | 13 | J1-19 | MISO |
| SX1262 DIO1 | 14 | J1-20 | DIO1 |
| SX1262 NRST | 17 | J1-10 | NRST / RESET |
| SX1262 BUSY | 18 | J1-11 | BUSY |

For the SX1262, also read off the module's datasheet, before power: its supply
voltage (3.3 V), whether it has a **TCXO** and at what voltage (sets
`AC_LORA_TCXO_V`), and whether **DIO2** drives its antenna switch (sets
`AC_LORA_DIO2_RFSW`). Write the module's name and those three facts into
`ac_pins.h` next to the SX1262 lines.

---

## SX1262 troubleshooting

`lib/ac/ac_lora.*`, HIL steps 0, 5 and 6. Codes are RadioLib's status codes as
printed by the smoke test and by `node_lora` (`SX1262 begin failed, RadioLib
code <n>`).

| Symptom | Most likely | What to do |
|---|---|---|
| smoke: `no version string: check NSS/SCK/MISO/MOSI, BUSY stuck, 3V3` | SPI wiring, or BUSY held high (module unpowered, NRST wrong) | Continuity-check the SPI lines against the table above; check 3V3 at the module |
| `begin` returns **-707** (SPI command timeout) | TCXO setting does not match the module | Crystal module: build with `-DAC_LORA_TCXO_V=0`. TCXO module: its datasheet voltage (1.6–3.3 V) |
| `begin` returns **-2** (chip not found) | NSS or MISO wrong, or a different radio chip | Check NSS=GPIO10, MISO=GPIO13; check the module really is an SX1262 |
| begins OK, transmits, the other side hears nothing | DIO2/RF-switch setting wrong, no antenna, or a different frequency/sync word | Toggle `-DAC_LORA_DIO2_RFSW`; fit antennas; build both boards from the same commit |
| hears packets, all dropped | Not ours (magic byte ≠ 0xAC), or CRC failures from range/antenna | Check both sides' builds; bring the boards closer; RSSI is in the gateway's stats |
| works on the bench, fails in the trailer | Range at SF9/125 kHz inside steel is unmeasured | Measure it before a demo that depends on it; that is HIL step 5's real result |
