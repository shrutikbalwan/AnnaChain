// AnnaChain — the gateway's way out: Wi-Fi to the server, and NTP for time.
//
// ╔══════════════════════════════════════════════════════════════════════════╗
// ║ UNPROVEN. This compiles against the ESP32 Arduino core. It has never     ║
// ║ joined a network or reached the server from a board: none has arrived.   ║
// ║ docs/HIL.md step 6.                                                      ║
// ╚══════════════════════════════════════════════════════════════════════════╝
//
// WifiHttpLink replaces SerialLink on the gateway. It speaks the same HTTP API
// backend/feed_sim.py uses — /api/lastack, /api/ingest, /api/gap — so nothing
// on the server changes. It is plain HTTP: fine on a bench network, not for a
// truck on the public internet. TLS (WiFiClientSecure with the server's CA) is
// the next step and is not done.
//
// 4G is not implemented. The buy list deliberately has no cellular modem; the
// gateway story is carried over Wi-Fi (a phone hotspot in the cab works). A 4G
// modem would be another ILink behind the same interface.
//
// NtpClock is an IClock. Until an NTP answer arrives it runs from the
// compiled-in date (kClockBase) plus uptime, exactly as ArduinoClock does, and
// it says which it is using. The server's timestamp check (checks.py, check 5)
// is what catches a clock that never synced.
#pragma once
#ifdef ARDUINO
#include <Arduino.h>
#include "ac_hal.h"

namespace ac {

// Connect to Wi-Fi, waiting at most timeoutMs. False if it did not connect.
bool wifiConnect(const char* ssid, const char* pass, uint32_t timeoutMs = 15000);

class NtpClock : public IClock {
 public:
  explicit NtpClock(uint32_t fallbackBase = kClockBase) : base_(fallbackBase) {}
  // Ask pool.ntp.org / time.google.com; needs Wi-Fi up. Waits at most timeoutMs.
  bool sync(uint32_t timeoutMs = 10000);
  bool synced() const { return synced_; }
  uint32_t now() override;
  void sleep(uint32_t ms) override { delay(ms); }
 private:
  uint32_t base_;
  bool synced_ = false;
};

class WifiHttpLink : public ILink {
 public:
  // baseUrl like "http://192.168.1.14:8000", no trailing slash.
  explicit WifiHttpLink(const char* baseUrl) : base_(baseUrl) {}
  bool up() override;
  bool queryLastAck(uint32_t device, uint32_t& lastAck) override;
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override;
  bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override;
  void setUp(bool u) { enabled_ = u; }       // the BOOT button still pulls the "antenna"
  int lastStatus() const { return status_; }
 private:
  String base_;
  bool enabled_ = true;
  int status_ = 0;
};

}  // namespace ac
#endif  // ARDUINO
