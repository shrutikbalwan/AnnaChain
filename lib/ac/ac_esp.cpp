#ifdef ARDUINO
#include "ac_esp.h"
#include "ac_sha256.h"
#include <Preferences.h>
#include <Wire.h>

#if __has_include(<Adafruit_SHT4x.h>)
  #include <Adafruit_SHT4x.h>
  #define AC_HAVE_SHT4X 1
  static Adafruit_SHT4x g_sht4;
#endif

namespace ac {

static const char* kLogPath = "/ac.log";
static const char* kAckPath = "/ac.ack";

// ── LittleFsStore ─────────────────────────────────────────────────────────
bool LittleFsStore::begin() {
  if (!LittleFS.begin(true)) return false;
  if (!LittleFS.exists(kLogPath)) {
    File f = LittleFS.open(kLogPath, "w");
    if (!f) return false;
    // Pre-size the ring so a full flash never surprises us mid-journey.
    uint8_t zero[kRecBytes] = {0};
    for (uint32_t i = 0; i < cap_; ++i) f.write(zero, kRecBytes);
    f.close();
  }
  return scan();
}

bool LittleFsStore::format() {
  LittleFS.remove(kLogPath);
  LittleFS.remove(kAckPath);
  first_ = last_ = ack_ = 0;
  return begin();
}

// Every slot carries its own sequence number, so after a power cut the highest
// valid sequence in the file is the truth. No separate index to go stale.
bool LittleFsStore::scan() {
  File f = LittleFS.open(kLogPath, "r");
  if (!f) return false;
  uint32_t hi = 0, lo = 0xFFFFFFFFu;
  uint8_t rec[kRecBytes];
  for (uint32_t i = 0; i < cap_; ++i) {
    if (f.read(rec, kRecBytes) != (int)kRecBytes) break;
    uint32_t seq = (uint32_t)rec[4] | (uint32_t)rec[5] << 8 |
                   (uint32_t)rec[6] << 16 | (uint32_t)rec[7] << 24;
    if (!seq) continue;
    if (seq > hi) hi = seq;
    if (seq < lo) lo = seq;
  }
  f.close();
  last_  = hi;
  first_ = hi ? lo : 0;
  ack_   = loadAck();
  return true;
}

bool LittleFsStore::append(const uint8_t rec[kRecBytes]) {
  uint32_t seq = (uint32_t)rec[4] | (uint32_t)rec[5] << 8 |
                 (uint32_t)rec[6] << 16 | (uint32_t)rec[7] << 24;
  File f = LittleFS.open(kLogPath, "r+");
  if (!f) return false;
  if (!f.seek(slot(seq) * kRecBytes)) { f.close(); return false; }
  size_t n = f.write(rec, kRecBytes);
  f.close();
  if (n != kRecBytes) return false;

  last_ = seq;
  if (!first_) first_ = seq;
  if (last_ - first_ + 1 > cap_) first_ = last_ - cap_ + 1;   // the ring wrapped
  return true;
}

bool LittleFsStore::read(uint32_t seq, uint8_t rec[kRecBytes]) {
  if (!seq || seq < first_ || seq > last_) return false;
  File f = LittleFS.open(kLogPath, "r");
  if (!f) return false;
  if (!f.seek(slot(seq) * kRecBytes)) { f.close(); return false; }
  int n = f.read(rec, kRecBytes);
  f.close();
  if (n != (int)kRecBytes) return false;

  uint32_t got = (uint32_t)rec[4] | (uint32_t)rec[5] << 8 |
                 (uint32_t)rec[6] << 16 | (uint32_t)rec[7] << 24;
  return got == seq;                       // the slot was reused: it is gone
}

uint32_t LittleFsStore::loadAck() {
  File f = LittleFS.open(kAckPath, "r");
  if (!f) return ack_;
  uint32_t v = 0;
  f.read((uint8_t*)&v, sizeof(v));
  f.close();
  ack_ = v;
  return v;
}

bool LittleFsStore::saveAck(uint32_t seq) {
  ack_ = seq;
  File f = LittleFS.open(kAckPath, "w");
  if (!f) return false;
  f.write((const uint8_t*)&seq, sizeof(seq));
  f.close();
  return true;
}

// ── Sht40Sensors ──────────────────────────────────────────────────────────
bool Sht40Sensors::begin() {
  Wire.begin(sda_, scl_);
  if (tamperPin_ >= 0) pinMode(tamperPin_, INPUT_PULLUP);
#ifdef AC_HAVE_SHT4X
  ready_ = g_sht4.begin(&Wire);
  if (ready_) {
    g_sht4.setPrecision(SHT4X_HIGH_PRECISION);
    g_sht4.setHeater(SHT4X_NO_HEATER);      // the heater costs power we do not have
  }
#endif
  return ready_;
}

Reading Sht40Sensors::read() {
  Reading v;
#ifdef AC_HAVE_SHT4X
  if (ready_) {
    sensors_event_t hum, tmp;
    if (g_sht4.getEvent(&hum, &tmp)) {
      v.temp = (int16_t)lroundf(tmp.temperature * 100.0f);
      v.rh   = (uint16_t)lroundf(hum.relative_humidity * 100.0f);
      v.ok   = true;
    } else {
      v.ok = false;
    }
  } else {
    v.ok = false;
  }
#else
  v.ok = false;
#endif
  v.c2h4 = kEthyleneNotFitted;              // no ethylene sensor chosen yet — say so
  if (tamperPin_ >= 0) v.tamper = (digitalRead(tamperPin_) == HIGH);
  if (battPin_ >= 0) {
    uint32_t mv = analogReadMilliVolts(battPin_) * 2;     // 2:1 divider
    int pct = (int)((mv - 3300) * 100 / (4200 - 3300));   // rough Li-ion curve
    v.batt = (uint8_t)(pct < 0 ? 0 : pct > 100 ? 100 : pct);
  } else {
    v.batt = 100;
  }
  return v;
}

// ── SerialLink ────────────────────────────────────────────────────────────
static void putHex(Stream& io, const uint8_t* p, size_t n) {
  static const char* d = "0123456789abcdef";
  for (size_t i = 0; i < n; ++i) { io.write(d[p[i] >> 4]); io.write(d[p[i] & 15]); }
}

bool SerialLink::waitAck(uint32_t& value) {
  uint32_t t0 = millis();
  String line;
  while (millis() - t0 < timeout_) {
    while (io_.available()) {
      char c = (char)io_.read();
      if (c == '\n') {
        line.trim();
        if (line.startsWith("A ")) { value = (uint32_t)line.substring(2).toInt(); return true; }
        if (line.startsWith("N "))  return false;      // the server said no
        line = "";
      } else if (c != '\r') {
        line += c;
        if (line.length() > 64) line = "";
      }
    }
    delay(2);
  }
  return false;                                         // timed out: treat as no link
}

bool SerialLink::queryLastAck(uint32_t device, uint32_t& lastAck) {
  if (!up_) return false;
  io_.printf("Q %u\n", device);
  return waitAck(lastAck);
}

bool SerialLink::send(const uint8_t* recs, size_t count, uint32_t& acked) {
  if (!up_) return false;
  io_.printf("B %u\n", (unsigned)count);      // how many records follow, then one ack
  for (size_t i = 0; i < count; ++i) {
    io_.print("R ");
    putHex(io_, recs + i * kRecBytes, kRecBytes);
    io_.print('\n');
  }
  return waitAck(acked);
}

bool SerialLink::declareGap(uint32_t device, uint32_t from, uint32_t to,
                            const uint8_t mac[32]) {
  if (!up_) return false;
  io_.printf("G %u %u %u ", device, from, to);    // G <device> <from> <to> <mac-hex>
  putHex(io_, mac, 32);
  io_.print('\n');
  uint32_t v = 0;
  return waitAck(v);
}

// ── EspSoftSigner ─────────────────────────────────────────────────────────
bool EspSoftSigner::begin() {
  Preferences prefs;
  prefs.begin("ac", false);
  size_t n = prefs.getBytes("k", key_, sizeof(key_));
  if (n != sizeof(key_)) {
    // First boot: make a key here and keep it here. It is not in the source,
    // and it does not leave the device — the same promise the ATECC608B makes,
    // with weaker hardware behind it.
    for (int i = 0; i < 32; ++i) key_[i] = (uint8_t)esp_random();
    prefs.putBytes("k", key_, sizeof(key_));
  }
  prefs.end();
  return true;
}

bool EspSoftSigner::sign(const uint8_t d[32], uint8_t sig[32]) {
  hmac_sha256(key_, sizeof(key_), d, 32, sig);
  return true;
}

// Development only. HMAC is symmetric, so the laptop needs the same key to
// check a signature — which is exactly the weakness the ATECC608B removes:
// with ECDSA the device keeps the private key and nobody else ever needs it.
// Delete this the day the secure element is fitted.
void EspSoftSigner::exportKeyHex(char out[65]) const { hex32(key_, out); }

bool EspSoftSigner::verify(const uint8_t d[32], const uint8_t sig[32]) {
  uint8_t want[32];
  hmac_sha256(key_, sizeof(key_), d, 32, want);
  return memcmp(want, sig, 32) == 0;
}

}  // namespace ac
#endif  // ARDUINO
