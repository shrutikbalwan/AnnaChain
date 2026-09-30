// AnnaChain — every GPIO the project uses, in one place, with its reason.
//
// Board:  ESP32-S3-DevKitC-1 v1.1 with ESP32-S3-WROOM-1-N16R8
//         (16 MB quad-SPI flash, 8 MB OCTAL-SPI PSRAM).
//
// Sources, and what each one was used for:
//   [UG]  Espressif, "ESP32-S3-DevKitC-1 v1.1" user guide, esp-dev-kits docs,
//         Header Block tables J1 and J3, and the note on GPIO35-37.
//         https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/esp32-s3-devkitc-1/user_guide_v1.1.html
//   [DS]  Espressif, "ESP32-S3 Series Datasheet" v2.2:
//         §2.3.5 Peripheral Pin Assignment (the P1-P4 priorities and the list
//         of restricted pins), Table 3-1 Default Configuration of Strapping
//         Pins, and the note in §4.2.2.1 SAR ADC ("ADC2_CH... cannot be used with Wi-Fi
//         simultaneously").
//         https://documentation.espressif.com/esp32-s3_datasheet_en.pdf
//
// Pins this project must NOT use, and why:
//   GPIO26-32  in-package / module flash and PSRAM (SPI0/1)            [DS §2.3.5 P4]
//   GPIO33-37  octal PSRAM data/DQS on N16R8. 35, 36 and 37 are on
//              header J3 (pins 11-13) but "not available for external
//              use" on octal-PSRAM boards; 33 and 34 are not broken
//              out at all.                                              [UG note; DS §2.3.5 P3]
//   GPIO0, 3, 45, 46  strapping pins: boot mode, JTAG source, VDD_SPI
//              voltage, ROM log. Their level at reset matters.          [DS Table 3-1]
//              (GPIO0 is the BOOT button; see kPinButton.)
//   GPIO19/20  USB_D-/USB_D+, the USB Serial/JTAG port we flash and
//              log through                                             [DS §2.3.5 P3; UG J3-19/20]
//   GPIO43/44  UART0 TX/RX, wired to the board's USB-UART bridge       [DS §2.3.5 P3; UG J3-2/3]
//   GPIO39-42  JTAG (MTCK/MTDO/MTDI/MTMS)                               [DS §2.3.5 P3; UG J3-6..9]
//   GPIO38     the RGB LED on board v1.1 (GPIO48 on the first version)  [UG J3-10, RGB LED note]
//   GPIO47/48  SPICLK_P/N differential clock pins, and 48 is the LED on
//              v1.0 boards                                              [UG J3-16/17]
//   ADC2 pins  (GPIO11-20) cannot be read while Wi-Fi is on, so nothing
//              analogue goes there                                      [DS §4.2.2.1 SAR ADC, note]
//
// Everything below is on the header, is not in any restricted list above,
// and is "Priority 2: can be freely used without restrictions" unless noted.
//
// UNVERIFIED ON HARDWARE. This map is checked against the documents above, not
// against a board: none has arrived. docs/BRINGUP.md is the procedure that
// checks it on the bench.
#pragma once
#include <stdint.h>

namespace ac {
namespace pins {

// ── I2C: SHT40 (0x44), ATECC608B (0x35 Trust&GO / 0x60 blank), PN532 (0x24) ──
// GPIO8 [UG J1-12] and GPIO9 [UG J1-15]: plain GPIOs, P2 [DS §2.3.5]. They
// are also the Arduino-ESP32 default Wire pins for the S3, so a library that
// calls Wire.begin() with no arguments lands on the same bus.
constexpr int kSda = 8;
constexpr int kScl = 9;

// ── node inputs ──────────────────────────────────────────────────────────
// Tamper reed loop, to GND when the lid is shut. GPIO4 [UG J1-4], P2.
// INPUT_PULLUP: a shut lid closes the reed and pulls GPIO4 LOW = sealed. HIGH
// (the pull-up wins) = the loop is open = FLAG_TAMPER. That is deliberate and
// it is fail-closed: a lid opened, a loop cut, a connector pulled and a reed
// missing all read HIGH, so all of them are reported. Inverting the sense (or
// using a pull-down) would make a cut or unplugged loop read as sealed, which
// is the one failure a tamper sensor must never have.
//
// So a BENCH board with nothing on GPIO4 reports tamper on every record, and
// that is correct: nothing is holding its seal shut. For bench work fit the
// jumper GPIO4 to GND (docs/HIL.md step 1), which is a closed loop by
// definition. Only builds that read the pin need it: node and node_lora
// (Sht40Sensors); node_mock's SimSensors never report tamper.
constexpr int kTamper  = 4;
// What a level on kTamper means. true = tamper. Kept here, not inline in the
// driver, so tools/selftest.cpp pins the fail-closed sense down.
constexpr bool tamperFromLevel(bool levelHigh) { return levelHigh; }
// Battery through a 2:1 divider. GPIO5 = ADC1_CH4 [UG J1-5]. It must be an
// ADC1 pin: ADC2 is unusable whenever Wi-Fi is on [DS §4.2.2.1].
constexpr int kBattery = 5;
// The BOOT button. GPIO0 is a strapping pin [DS Table 3-1: weak pull-up,
// selects boot mode at reset]. The board's own button is the only thing on it;
// reading it after reset is allowed ("the pins are freed up to be used as
// regular IO pins after reset" [DS §3]). Never wire anything else here.
constexpr int kButton  = 0;

// ── PN532 NFC, I2C mode ─────────────────────────────────────────────────
// The Adafruit driver wants an IRQ and a reset line even on I2C.
// GPIO6 [UG J1-6] and GPIO7 [UG J1-7]: ADC1 pins used as plain GPIO, P2.
constexpr int kNfcIrq   = 6;
constexpr int kNfcReset = 7;

// ── SX1262 LoRa, on the FSPI host ───────────────────────────────────────
// GPIO10-13 are the FSPI signals' IO MUX pins (FSPICS0, FSPID, FSPICLK,
// FSPIQ) [UG J1-16..19], i.e. Priority 1 for SPI2 [DS §2.3.5] — the fastest,
// least surprising choice for an SPI peripheral.
constexpr int kLoraNss  = 10;   // FSPICS0  [UG J1-16]
constexpr int kLoraMosi = 11;   // FSPID    [UG J1-17]
constexpr int kLoraSck  = 12;   // FSPICLK  [UG J1-18]
constexpr int kLoraMiso = 13;   // FSPIQ    [UG J1-19]
// DIO1 (the interrupt): GPIO14 [UG J1-20], P2.
constexpr int kLoraDio1 = 14;
// NRST and BUSY: GPIO17 [UG J1-10] and GPIO18 [UG J1-11] — plain GPIO, P2;
// their alternative functions are UART1 TX/RX, and UART1 is not used.
//
// CHANGED from the earlier comment in src/gateway.cpp, which put NRST on
// GPIO15 and BUSY on GPIO16. Those are XTAL_32K_P/XTAL_32K_N [UG J1-8/9]:
// usable as GPIO only while no 32 kHz crystal is fitted, and an external
// crystal is the obvious fix for the node's sleep-clock drift. 17 and 18 carry
// no such dependency.
constexpr int kLoraReset = 17;
constexpr int kLoraBusy  = 18;

}  // namespace pins
}  // namespace ac
