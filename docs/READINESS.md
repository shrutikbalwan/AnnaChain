<!-- The hardware-readiness brief, as pasted into the build session (30 Sep 2026). Reproduced as received. Work done against it: lib/ac/ac_pins.h, the drivers in lib/ac/ac_{lora,nfc,atecc,net}.*, src/smoke.cpp and platformio.ini; see also docs/HIL.md and docs/BRINGUP.md. -->

AnnaChain — hardware readiness brief

The software is complete and verified: 92 firmware checks, 124 backend tests, C1–C9 passing. The gap is now entirely hardware. Boards are on order and not yet here.

You cannot test any of this. That is the constraint that shapes everything below. Your job is to remove every failure that is not physical, so that arrival day is spent on wiring and measurement rather than on compile errors and missing libraries.

Ground rules

Never describe untested hardware code as working. It compiles. Say exactly that, in the code and in the docs. A driver that has never seen its chip is unproven no matter how carefully written.
Do not invent pin assignments. Every GPIO must be justified against the official ESP32-S3-DevKitC-1 pinout and the module's datasheet, cited in a comment. Specific trap: the N16R8 variant uses octal PSRAM, which occupies GPIO 33–37 — those pins are unusable. Also check strapping pins (0, 3, 45, 46), USB (19, 20) and UART0 (43, 44). The pin numbers currently in src/main.cpp and the comments in src/gateway.cpp were chosen plausibly and have not been checked against any of this. Verify them; change them if they are wrong; say which you changed.
Compile-verify everything. Install PlatformIO and make every environment build, including the ones that need real libraries.

Task 1 — make the five hardware drivers compile

Write them properly against real libraries, and make pio run succeed for each:

LoraRadio (RadioLib, SX1262, IN865 — 865–867 MHz, not 433 or 915)
AteccSigner replacing EspSoftSigner — real ECDSA, key generated on-chip, private key never readable
PN532 NFC: shipment assignment at the pack-house, custody taps at checkpoints
A Wi-Fi/4G uplink replacing SerialLink on the gateway
NTP time sync, with the compiled-in date as fallback

Each must sit behind the existing interface so nothing above it changes, and each must carry a header comment saying it is unproven.

Task 2 — a peripheral smoke test

A separate PlatformIO target that probes each part in turn and prints a plain verdict: I²C scan, SHT40 identity and a plausible reading, ATECC608B serial number and lock state, SX1262 version register, PN532 firmware version, battery ADC, flash mount. One line per part, OK or FAIL with the reason. This is the first thing that runs on arrival day and it must tell you which part is wrong in under a minute.

Task 3 — the bring-up guide

docs/BRINGUP.md: verified pin map as a table, wiring diagram, and a numbered checklist from unboxing to a logging node. Plus a troubleshooting table of what actually goes wrong — board not enumerating, wrong COM port, I²C address clash, SHT40 returning NaN, LoRa never receiving, ATECC608B already locked from the factory.

Task 4 — one command to run the demo

make demo-full or a script: build, wipe the database, start the server, generate a fresh capture, feed it, open the browser. Every setup failure in this project so far has been a typo or a stale file. Remove the opportunity.

Task 5 — measure the power claim

The deck claims 36 µA idle and 3.8 years of battery life. That is computed, not measured. Write docs/POWER.md: the measurement procedure with a specific method, what to record, and a script that turns the readings into the same figures the deck quotes — so the claim can be confirmed or corrected with evidence. If the measurement disagrees with the calculation, the deck changes, not the measurement.

Task 6 — the hardware test plan

docs/HIL.md: for each part, the test that proves it works, its expected output, and which deck claim it supports. Ordered so that step 1 needs only a bare board and step 6 needs everything.

Acceptance

Every PlatformIO environment compiles, including all five drivers
The smoke test builds and its serial output format is documented
92 firmware checks and 124 backend tests still pass, unchanged
Every pin in the project is justified by a citation
Every untested driver says so, in the code and in backend/README.md
