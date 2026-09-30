// AnnaChain — the PN532 NFC reader: who took custody of this crate, and when.
//
// ╔══════════════════════════════════════════════════════════════════════════╗
// ║ UNPROVEN. This compiles against the Adafruit PN532 library. It has never ║
// ║ read a tag: no PN532 has arrived. docs/HIL.md step 3.                    ║
// ╚══════════════════════════════════════════════════════════════════════════╝
//
// Two uses, one mechanism: a tap is a tag UID seen at a moment.
//   * At the pack-house, the first tap after a fresh start ASSIGNS the node to
//     a shipment (EPCIS "commissioning").
//   * Every later tap is a CUSTODY event at a checkpoint (EPCIS "inspecting").
// The node prints each tap on its serial line as
//     T <device> <assign|tap> <uid-hex> <unix-time>
// and backend/bridge_serial.py turns it into a checkpoint on the server.
//
// Not yet done, and said plainly: a tap is not signed and is not in the hash
// chain. It is a logged claim, weaker than a reading. Over LoRa it is not
// carried at all yet. Both are named in backend/README.md.
//
// Wiring: I2C mode, on the shared bus with the SHT40 (PN532 7-bit address
// 0x24). Most PN532 boards select the interface with two DIP switches or
// jumpers; set them to I2C, or begin() will find nothing.
#pragma once
#ifdef ARDUINO
#include <Arduino.h>
#include <Wire.h>

namespace ac {

struct NfcTap {
  uint8_t uid[10];
  uint8_t len = 0;
  bool    assign = false;       // the first tap since a fresh start
};

class Pn532Reader {
 public:
  Pn532Reader(int irqPin, int resetPin, TwoWire& wire = Wire)
      : irq_(irqPin), rst_(resetPin), wire_(wire) {}

  // True if a PN532 answered with a firmware version.
  bool begin();
  // IC and firmware version as the chip reports them (0 if absent).
  uint32_t firmwareVersion() const { return fw_; }

  // Look for a tag for up to timeoutMs. The same tag held against the reader
  // counts once, not once per poll.
  bool poll(NfcTap& tap, uint16_t timeoutMs = 50);

  // A fresh start (the `wipe` command): the next tap is an assignment again.
  void resetAssignment() { assigned_ = false; }

 private:
  int irq_, rst_;
  TwoWire& wire_;
  uint32_t fw_ = 0;
  bool assigned_ = false;
  uint8_t last_[10] = {0};
  uint8_t lastLen_ = 0;
  uint32_t lastAt_ = 0;
};

}  // namespace ac
#endif  // ARDUINO
