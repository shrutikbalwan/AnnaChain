// AnnaChain — node firmware for the ESP32-S3.
//
//   pio run -e node_mock -t upload     fake sensor data, works with a bare board
//   pio run -e node      -t upload     real SHT40 on I2C
//
// Then on the laptop:  python3 tools/server.py /dev/ttyACM0
//
// The BOOT button is the antenna. Press it and the link goes down; the node
// keeps sampling and keeps storing. Press it again and the gap fills itself.
#include <Arduino.h>
#include "ac_node.h"
#include "ac_esp.h"
#include "ac_sim.h"          // SimSensors doubles as the mock sensor on-board

using namespace ac;

// ── wiring ────────────────────────────────────────────────────────────────
static const uint32_t kDeviceId   = 0x26232001;   // per node; must be registered server-side
static const uint8_t  kPinSDA     = 8;            // SHT40
static const uint8_t  kPinSCL     = 9;
static const int      kPinTamper  = 4;            // enclosure loop, to GND when closed
static const int      kPinBattery = 5;            // 2:1 divider off the cell
static const int      kPinButton  = 0;            // BOOT — stands in for the antenna
static const uint32_t kSampleMs   = 5000;         // 5 s on the bench; 300000 in the field

// ── the pieces ────────────────────────────────────────────────────────────
static ArduinoClock  clk(kClockBase);             // 25 Sep 2026; set properly from the server
static LittleFsStore store(AC_LOG_CAPACITY);
static EspSoftSigner signer;
static SerialLink    link(Serial);

#ifdef AC_MOCK_SENSORS
static SimSensors    sensors(42);
#else
static Sht40Sensors  sensors(kPinSDA, kPinSCL);
#endif

static Node node(kDeviceId, clk, sensors, store, link, signer);

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
  char k[65]; signer.exportKeyHex(k);
  Serial.printf("K %u %s\n", kDeviceId, k);   // dev only: lets the laptop check signatures
  Serial.println("# press BOOT to drop the link, press again to restore it");
  Serial.println("# type 'wipe' to clear the flash and start a fresh run");
}

void setup() {
  Serial.begin(115200);
  delay(400);
  pinMode(kPinButton, INPUT_PULLUP);

#ifndef AC_MOCK_SENSORS
  sensors.setTamperPin(kPinTamper);
  sensors.setBatteryPin(kPinBattery);
#endif

  if (!node.begin()) {
    Serial.println("# FLASH FAILED — nothing can be trusted, halting");
    while (true) delay(1000);
  }
  banner();
}

void loop() {
  // The antenna pull.
  bool b = digitalRead(kPinButton);
  if (lastButton && !b) {                           // pressed
    link.setUp(!link.up());
    Serial.printf("\n# LINK %s  (stored %u, waiting %u)\n",
                  link.up() ? "UP — filling the gap" : "DOWN — still logging",
                  store.lastSeq(), node.pending());
    if (link.up()) node.resync();
    delay(200);                                     // debounce
  }
  lastButton = b;

  // A way to start the demo clean without reflashing.
  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "wipe") { store.format(); node.begin(); Serial.println("# flash wiped"); banner(); }
    else if (cmd == "stat") banner();
  }

  if (millis() - lastSample >= kSampleMs) {
    lastSample = millis();
    node.tick();                                    // sense, chain, sign, store, then radio

    uint8_t raw[kRecBytes];
    if (store.read(store.lastSeq(), raw)) {
      Record r; decode(raw, r);
      Serial.printf("# %5u  %6.2f C  %5.2f %%  batt %3u  %s  waiting %u\n",
                    r.seq, r.tempC(), r.humidity(), r.batt,
                    link.up() ? "sent" : "HELD", node.pending());
    }
  }
}
