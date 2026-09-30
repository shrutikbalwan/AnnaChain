// UNPROVEN — compiles; never run against a PN532. See ac_nfc.h.
#ifdef ARDUINO
#include "ac_nfc.h"

#if __has_include(<Adafruit_PN532.h>)
#include <Adafruit_PN532.h>

namespace ac {

static Adafruit_PN532* g_nfc = nullptr;

bool Pn532Reader::begin() {
  if (!g_nfc) g_nfc = new Adafruit_PN532((uint8_t)irq_, (uint8_t)rst_, &wire_);
  if (!g_nfc->begin()) { fw_ = 0; return false; }
  fw_ = g_nfc->getFirmwareVersion();       // 0 when nothing answered
  if (!fw_) return false;
  g_nfc->SAMConfig();                      // normal mode, ready to read tags
  return true;
}

bool Pn532Reader::poll(NfcTap& tap, uint16_t timeoutMs) {
  if (!g_nfc || !fw_) return false;
  uint8_t uid[10]; uint8_t len = 0;
  if (!g_nfc->readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &len, timeoutMs))
    return false;
  if (len > sizeof(uid)) len = sizeof(uid);

  // The same tag within five seconds is the same tap, still held to the reader.
  bool same = len == lastLen_ && !memcmp(uid, last_, len);
  if (same && millis() - lastAt_ < 5000) { lastAt_ = millis(); return false; }
  memcpy(last_, uid, len); lastLen_ = len; lastAt_ = millis();

  memcpy(tap.uid, uid, len);
  tap.len = len;
  tap.assign = !assigned_;
  assigned_ = true;
  return true;
}

}  // namespace ac

#else
namespace ac {
bool Pn532Reader::begin() { return false; }
bool Pn532Reader::poll(NfcTap&, uint16_t) { return false; }
}  // namespace ac
#endif
#endif  // ARDUINO
