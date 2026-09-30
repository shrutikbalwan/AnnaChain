// AnnaChain — node firmware for the ESP32-S3.
//
//   pio run -e node_mock -t upload     fake sensor data, works with a bare board
//   pio run -e node      -t upload     real SHT40 on I2C, USB serial link
//   pio run -e node_lora -t upload     real SHT40, SX1262 to the gateway, PN532 taps
//
// Then on the laptop, for the USB builds:  python backend/bridge_serial.py COM5
//
// The BOOT button is the antenna. Press it and the link goes down; the node
// keeps sampling and keeps storing. Press it again and the gap fills itself.
//
// node_mock and node use only parts of the design that already run on the
// laptop (the USB serial link, LittleFS). node_lora adds the SX1262 and PN532
// drivers, which are UNPROVEN: all three environments compile (30 Sep
// 2026), and the drivers have never met their chips.
// Every pin comes from lib/ac/ac_pins.h, where each one is justified.
//
// Arrival day (docs/HIL.md): node_mock is step 1, node is step 2, node_lora is
// steps 3 (PN532 taps) and 5 (records over the SX1262 to the gateway).
#include <Arduino.h>
#include "ac_node.h"
#include "ac_esp.h"
#include "ac_pins.h"
#include "ac_sim.h"          // SimSensors doubles as the mock sensor on-board
#ifdef AC_LINK_LORA
#include "ac_lora.h"
#endif
#ifdef AC_NFC
#include "ac_nfc.h"
#endif

using namespace ac;

static const uint32_t kDeviceId = 0x26232001;   // per node; must be enrolled server-side
static const uint32_t kSampleMs = 5000;         // 5 s on the bench; 300000 in the field

// ── the pieces ────────────────────────────────────────────────────────────
static ArduinoClock  clk(kClockBase);            // 25 Sep 2026; set properly from the server
static LittleFsStore store(AC_LOG_CAPACITY);
static EspSoftSigner signer;

#ifdef AC_LINK_LORA
static Sx1262Transport lora(pins::kLoraNss, pins::kLoraDio1, pins::kLoraReset,
                            pins::kLoraBusy, pins::kLoraSck, pins::kLoraMiso,
                            pins::kLoraMosi);
static LoraNodeLink  nodeLink(lora);   // not "link": POSIX link() is in scope
#else
static SerialLink    nodeLink(Serial); // not "link": POSIX link() is in scope
#endif

#ifdef AC_MOCK_SENSORS
static SimSensors    sensors(42);
#else
static Sht40Sensors  sensors(pins::kSda, pins::kScl);
#endif

#ifdef AC_NFC
static Pn532Reader   nfc(pins::kNfcIrq, pins::kNfcReset);
static bool          nfcOk = false;
#endif

static Node node(kDeviceId, clk, sensors, store, nodeLink, signer);

static uint32_t lastSample = 0;
static bool     lastButton = true;

static void banner() {
  Serial.printf("\n# AnnaChain node %08X\n", kDeviceId);
  Serial.printf("# flash ring %u records, holding %u, last seq %u, server has %u\n",
                store.capacity(), store.count(), store.lastSeq(), node.ackedSeq());
#ifdef AC_MOCK_SENSORS
  Serial.println("# sensors: MOCK (no parts needed)");
#else
  Serial.println("# sensors: SHT40 on I2C");
#endif
#ifdef AC_LINK_LORA
  Serial.printf("# link: SX1262 LoRa %s (UNPROVEN driver)\n", lora.ready() ? "up" : "NOT READY");
#else
  Serial.println("# link: USB serial");
#endif
#ifdef AC_NFC
  Serial.printf("# NFC: PN532 %s (UNPROVEN driver)\n", nfcOk ? "ready" : "NOT FOUND");
#endif
  char k[65]; signer.exportKeyHex(k);
  Serial.printf("K %u %s\n", kDeviceId, k);   // dev only: lets the laptop check signatures
  Serial.println("# press BOOT to drop the link, press again to restore it");
  Serial.println("# type 'wipe' to clear the flash and start a fresh run");
}

void setup() {
  Serial.begin(115200);
  delay(400);
  pinMode(pins::kButton, INPUT_PULLUP);

#ifndef AC_MOCK_SENSORS
  sensors.setTamperPin(pins::kTamper);
  sensors.setBatteryPin(pins::kBattery);
#endif

  if (!node.begin()) {
    Serial.println("# FLASH FAILED — nothing can be trusted, halting");
    while (true) delay(1000);
  }
#ifdef AC_LINK_LORA
  int16_t rc = lora.begin();
  if (rc) Serial.printf("# SX1262 begin failed, RadioLib code %d — still logging\n", rc);
#endif
#ifdef AC_NFC
  nfcOk = nfc.begin();                          // after node.begin(): the I2C bus is up
#endif
  banner();
}

#ifdef AC_NFC
static void pollNfc() {
  NfcTap tap;
  if (!nfcOk || !nfc.poll(tap, 30)) return;
  char uid[21] = {0};
  for (uint8_t i = 0; i < tap.len && i < 10; ++i) sprintf(uid + 2 * i, "%02x", tap.uid[i]);
  // T <device> <assign|tap> <uid> <unix>: bridge_serial.py makes a checkpoint of it.
  Serial.printf("T %u %s %s %u\n", kDeviceId, tap.assign ? "assign" : "tap", uid, clk.now());
}
#endif

void loop() {
  // The antenna pull.
  bool b = digitalRead(pins::kButton);
  if (lastButton && !b) {                           // pressed
    nodeLink.setUp(!nodeLink.up());
    Serial.printf("\n# LINK %s  (stored %u, waiting %u)\n",
                  nodeLink.up() ? "UP — filling the gap" : "DOWN — still logging",
                  store.lastSeq(), node.pending());
    if (nodeLink.up()) node.resync();
    delay(200);                                     // debounce
  }
  lastButton = b;

  // A way to start the demo clean without reflashing.
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "wipe") {
      store.format(); node.begin();
#ifdef AC_NFC
      nfc.resetAssignment();
#endif
      Serial.println("# flash wiped"); banner();
    }
    else if (cmd == "stat") banner();
  }

#ifdef AC_NFC
  pollNfc();
#endif

  if (millis() - lastSample >= kSampleMs) {
    lastSample = millis();
    node.tick();                                    // sense, chain, sign, store, then radio

    uint8_t raw[kRecBytes];
    if (store.read(store.lastSeq(), raw)) {
      Record r; decode(raw, r);
      Serial.printf("# %5u  %6.2f C  %5.2f %%  batt %3u  %s  waiting %u\n",
                    r.seq, r.tempC(), r.humidity(), r.batt,
                    nodeLink.up() ? "sent" : "HELD", node.pending());
    }
  }
}
