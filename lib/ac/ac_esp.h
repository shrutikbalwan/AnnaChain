// AnnaChain — the real board.
//
// Everything here is the ESP32-S3 half of the interfaces in ac_hal.h. The node
// logic in ac_node.cpp does not know which half it is talking to, which is why
// the laptop demo and the board run identical code.
#pragma once
#ifdef ARDUINO

#include <Arduino.h>
#include <LittleFS.h>
#include "ac_hal.h"

namespace ac {

// ── clock ─────────────────────────────────────────────────────────────────
// Until an RTC or an NTP sync is on the board, time runs from a base set at
// boot. The server checks timestamps for plausibility, so a wrong base shows
// up as a rejected record rather than a silently wrong history.
class ArduinoClock : public IClock {
 public:
  explicit ArduinoClock(uint32_t base) : base_(base) {}
  uint32_t now() override { return base_ + (uint32_t)(millis() / 1000); }
  void sleep(uint32_t ms) override { delay(ms); }
  void setBase(uint32_t unixSeconds) { base_ = unixSeconds - (uint32_t)(millis()/1000); }
 private:
  uint32_t base_;
};

// ── the flash log ─────────────────────────────────────────────────────────
// One fixed-size file used as a ring, plus a tiny ack file. Fixed-size records
// mean a sequence number is an offset — no index to corrupt, and a half-written
// record at the end of a power cut is detectable and discarded.
class LittleFsStore : public IStore {
 public:
  explicit LittleFsStore(uint32_t capacity) : cap_(capacity) {}
  bool begin() override;
  bool append(const uint8_t rec[kRecBytes]) override;
  bool read(uint32_t seq, uint8_t rec[kRecBytes]) override;
  uint32_t firstSeq() const override { return first_; }
  uint32_t lastSeq()  const override { return last_; }
  uint32_t count()    const override { return first_ ? last_ - first_ + 1 : 0; }
  uint32_t capacity() const override { return cap_; }
  uint32_t loadAck() override;
  bool     saveAck(uint32_t seq) override;
  bool     format();                       // wipe, for a clean demo run

 private:
  uint32_t slot(uint32_t seq) const { return (seq - 1) % cap_; }
  bool scan();                             // find first_/last_ after a reboot
  uint32_t cap_, first_ = 0, last_ = 0, ack_ = 0;
};

// ── sensors ───────────────────────────────────────────────────────────────
// The real SHT40 over I2C. Returns ok=false rather than a made-up number when
// the part does not answer — a flagged reading is honest, an invented one is not.
class Sht40Sensors : public ISensors {
 public:
  Sht40Sensors(uint8_t sdaPin, uint8_t sclPin) : sda_(sdaPin), scl_(sclPin) {}
  bool begin() override;
  Reading read() override;
  void setTamperPin(int pin)   { tamperPin_ = pin; }
  void setBatteryPin(int pin)  { battPin_ = pin; }
 private:
  uint8_t sda_, scl_;
  int tamperPin_ = -1, battPin_ = -1;
  bool ready_ = false;
};

// ── the radio, standing in as USB serial ──────────────────────────────────
// For Evaluation 1 the "network" is the USB cable and tools/server.py on a
// laptop. Pressing the BOOT button is the antenna pull. When the SX1262 arrives
// this class is replaced and nothing above it changes.
class SerialLink : public ILink {
 public:
  explicit SerialLink(Stream& io) : io_(io) {}
  bool up() override { return up_; }
  bool queryLastAck(uint32_t device, uint32_t& lastAck) override;
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override;
  bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override;
  void setUp(bool u) { up_ = u; }
  void setTimeout(uint32_t ms) { timeout_ = ms; }
 private:
  bool waitAck(uint32_t& value);
  Stream&  io_;
  bool     up_ = true;
  uint32_t timeout_ = 3000;
};

// ── the secure element ────────────────────────────────────────────────────
// HMAC in software until the ATECC608B is fitted; the key lives in NVS, not in
// the source. Replace with AteccSigner and the call sites do not move.
class EspSoftSigner : public ISigner {
 public:
  bool begin() override;
  bool sign(const uint8_t d[32], uint8_t sig[32]) override;
  bool verify(const uint8_t d[32], const uint8_t sig[32]) override;
  void exportKeyHex(char out[65]) const;   // development only — see the .cpp
 private:
  uint8_t key_[32] = {0};
};

}  // namespace ac
#endif  // ARDUINO
