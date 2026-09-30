// AnnaChain — arrival-day smoke test. The first thing that runs on a new board.
//
//   pio run -e smoke -t upload && pio device monitor
//
// It probes each part in turn and prints one line per part, then a verdict.
// It is meant to tell you WHICH part is wrong, in well under a minute, before
// anyone starts debugging the node firmware. Output format (docs/BRINGUP.md):
//
//   AC-SMOKE v1 build <date>
//   <PART>   <OK|FAIL|SKIP>  <detail>
//   ...
//   AC-SMOKE RESULT <ok>/<total> OK[  FAIL: <part>, <part>]
//
// Parts, in order: BOARD, FLASH, I2C, SHT40, ATECC, SX1262, PN532, BATT.
// Type r and Enter to run it again (after re-seating a wire, say).
//
// Like the drivers it calls, this is UNPROVEN: it compiles, and has never run
// on the hardware it probes. Its first real run is its own first test.
#include <Arduino.h>
#include <Wire.h>
#include <LittleFS.h>
#include "ac_pins.h"
#include "ac_sha256.h"
#include "ac_atecc.h"
#include "ac_lora.h"
#include "ac_nfc.h"
#include <Adafruit_SHT4x.h>

using namespace ac;

static int nOk = 0, nTotal = 0;
static String failed;

static void line(const char* part, bool ok, const String& detail, bool skip = false) {
  nTotal++;
  if (ok) nOk++;
  else if (!skip) { if (failed.length()) failed += ", "; failed += part; }
  Serial.printf("%-7s %-5s %s\n", part, skip ? "SKIP" : ok ? "OK" : "FAIL", detail.c_str());
}

static String hex2(uint8_t v) { char b[5]; snprintf(b, sizeof b, "0x%02X", v); return b; }

// ── the probes ────────────────────────────────────────────────────────────
static void probeBoard() {
  uint32_t flash = ESP.getFlashChipSize(), psram = ESP.getPsramSize();
  String d = String(ESP.getChipModel()) + " rev " + ESP.getChipRevision() +
             ", flash " + (flash >> 20) + " MB, PSRAM " + (psram >> 20) + " MB";
  // N16R8: 16 MB flash, 8 MB PSRAM. 0 MB PSRAM almost always means the build
  // was not made with memory_type qio_opi (see platformio.ini).
  bool ok = flash >= (16u << 20) && psram >= (8u << 20);
  if (!ok) d += "  (expected 16 MB / 8 MB for an N16R8)";
  line("BOARD", ok, d);
}

static void probeFlash() {
  if (!LittleFS.begin(true)) { line("FLASH", false, "LittleFS would not mount or format"); return; }
  const char* p = "/smoke.tmp";
  const char msg[] = "annachain";
  File f = LittleFS.open(p, "w");
  bool ok = f && f.write((const uint8_t*)msg, sizeof msg) == sizeof msg;
  if (f) f.close();
  char back[sizeof msg] = {0};
  f = LittleFS.open(p, "r");
  ok = ok && f && f.read((uint8_t*)back, sizeof back) == (int)sizeof back && !memcmp(back, msg, sizeof msg);
  if (f) f.close();
  LittleFS.remove(p);
  line("FLASH", ok, String("LittleFS ") + (LittleFS.totalBytes() >> 10) + " KB, " +
                    (LittleFS.usedBytes() >> 10) + " KB used, write/read " + (ok ? "ok" : "FAILED"));
}

static bool seen[128];

static const char* whoIs(uint8_t a) {
  switch (a) {
    case 0x44: return "SHT40";
    case 0x35: return "ATECC608B-TNGTLS";
    case 0x60: return "ATECC608 (blank)";
    case 0x24: return "PN532";
    default:   return "?";
  }
}

static void probeI2c() {
  Wire.begin(pins::kSda, pins::kScl);
  Wire.setTimeOut(50);
  memset(seen, 0, sizeof seen);
  String d; int n = 0;
  for (uint8_t a = 0x08; a < 0x78; ++a) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      seen[a] = true; n++;
      d += " " + hex2(a) + " " + whoIs(a);
    }
  }
  // An ATECC608 sleeps and ignores its address until woken, so it may be
  // absent from this scan and still be fine; the ATECC line below wakes it.
  if (!n) line("I2C", false, String("no devices on SDA=GPIO") + pins::kSda + " SCL=GPIO" +
                             pins::kScl + ": check 3V3, GND, pull-ups, SDA/SCL swapped");
  else    line("I2C", true, String(n) + " device(s):" + d);
}

static Adafruit_SHT4x sht;

static void probeSht40() {
  if (!sht.begin(&Wire)) { line("SHT40", false, "no answer at 0x44"); return; }
  uint32_t serial = sht.readSerial();
  sht.setPrecision(SHT4X_HIGH_PRECISION);
  sht.setHeater(SHT4X_NO_HEATER);
  sensors_event_t h, t;
  if (!sht.getEvent(&h, &t)) { line("SHT40", false, "answered, but a measurement failed"); return; }
  float c = t.temperature, rh = h.relative_humidity;
  char d[96];
  snprintf(d, sizeof d, "serial 0x%08lX, %.2f C, %.1f %%RH", (unsigned long)serial, c, rh);
  // Plausible on a bench: inside the part's rated range, and not the all-zero
  // or NaN a broken read produces.
  bool ok = !isnan(c) && !isnan(rh) && c > -40 && c < 85 && rh > 0 && rh <= 100 && serial != 0;
  line("SHT40", ok, String(d) + (ok ? "" : "  (implausible)"));
}

static AteccSigner atecc;

static void probeAtecc() {
  bool up = atecc.begin();
  if (!atecc.present()) { line("ATECC", false, atecc.lastError()); return; }
  String d = "addr " + hex2(atecc.address()) + ", serial " + atecc.serialNumber() +
             (atecc.locked() ? ", locked" : ", UNLOCKED");
  if (!up) { line("ATECC", false, d + ": " + atecc.lastError()); return; }

  uint8_t pub[64], sig[64], dg[32];
  Sha256::hash((const uint8_t*)"AnnaChain smoke test", 20, dg);
  if (!atecc.publicKey(pub)) { line("ATECC", false, d + ", public key: " + atecc.lastError()); return; }
  if (!atecc.sign(dg, sig))  { line("ATECC", false, d + ", sign: " + atecc.lastError()); return; }
  // Checked by mbedTLS, not by the chip: a standard P-256 signature or nothing.
  bool v = ecdsaVerifySoftware(pub, dg, sig);
  sig[5] ^= 1;
  bool neg = !ecdsaVerifySoftware(pub, dg, sig);    // and a damaged one must fail
  line("ATECC", v && neg, d + ", slot 0 ECDSA " + (v && neg ? "sign+verify ok" : "verify FAILED"));
}

static Sx1262Transport lora(pins::kLoraNss, pins::kLoraDio1, pins::kLoraReset,
                            pins::kLoraBusy, pins::kLoraSck, pins::kLoraMiso, pins::kLoraMosi);

static void probeSx1262() {
  char ver[17];
  bool v = lora.versionString(ver);
  if (!v) { line("SX1262", false, "no version string: check NSS/SCK/MISO/MOSI, BUSY stuck, 3V3"); return; }
  int16_t rc = lora.begin();
  char d[120];
  snprintf(d, sizeof d, "version \"%s\", begin at %.4f MHz %s (RadioLib %d)",
           ver, (double)AC_LORA_FREQ_MHZ, rc == 0 ? "ok" : "FAILED", rc);
  // -707 is RadioLib's "SPI command timeout", classically a wrong TCXO setting.
  line("SX1262", rc == 0, String(d) + (rc == -707 ? "  (try AC_LORA_TCXO_V=0)" : ""));
}

static Pn532Reader nfc(pins::kNfcIrq, pins::kNfcReset);

static void probePn532() {
  if (!nfc.begin()) {
    line("PN532", false, "no firmware version: set the board's DIP switches to I2C, check 0x24");
    return;
  }
  uint32_t v = nfc.firmwareVersion();
  char d[64];
  snprintf(d, sizeof d, "IC PN5%02lX, firmware %lu.%lu", (unsigned long)((v >> 24) & 0xFF),
           (unsigned long)((v >> 16) & 0xFF), (unsigned long)((v >> 8) & 0xFF));
  line("PN532", true, d);
}

static void probeBattery() {
  pinMode(pins::kBattery, INPUT);
  uint32_t sum = 0;
  for (int i = 0; i < 8; ++i) sum += analogReadMilliVolts(pins::kBattery);
  uint32_t mv = sum / 8 * 2;                        // 2:1 divider on the board
  char d[80];
  snprintf(d, sizeof d, "%lu mV at the cell (GPIO%d x2)", (unsigned long)mv, pins::kBattery);
  if (mv < 500) { line("BATT", false, String(d) + ": no divider or no cell"); return; }
  bool ok = mv >= 2800 && mv <= 4300;               // a Li-ion or LiFePO4 cell, roughly
  line("BATT", ok, String(d) + (ok ? "" : "  (outside 2.8-4.3 V: check the divider)"));
}

static void runAll() {
  nOk = nTotal = 0; failed = "";
  uint32_t t0 = millis();
  Serial.printf("\nAC-SMOKE v1 build %s %s\n", __DATE__, __TIME__);
  probeBoard();
  probeFlash();
  probeI2c();
  probeSht40();
  probeAtecc();
  probeSx1262();
  probePn532();
  probeBattery();
  Serial.printf("AC-SMOKE RESULT %d/%d OK%s%s  (%lu ms)\n", nOk, nTotal,
                failed.length() ? "  FAIL: " : "", failed.c_str(),
                (unsigned long)(millis() - t0));
  Serial.println("type r to run again");
}

void setup() {
  Serial.begin(115200);
  delay(1500);          // time to open the monitor after a reset
  runAll();
}

void loop() {
  if (Serial.available() && Serial.read() == 'r') runAll();
  delay(20);
}
