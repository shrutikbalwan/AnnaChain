// AnnaChain — gateway firmware for the ESP32-S3 in the truck cab.
//
//   pio run -e gateway -t upload
//
// One of these per truck. Every crate node sends to it over LoRa; it buffers
// what it hears and pushes it upstream whenever there is a signal.
//
// It holds no signing key and never will. Records cross it byte for byte, are
// verified at the server against the node's own signature, and a gateway that
// is tampered with can therefore delay or lose data but cannot invent it. If
// you are ever tempted to parse a reading in here and correct it, don't — that
// is the oracle problem coming back in through the side door.
//
// The SX1262 driver is the one piece that needs the real module in hand. It is
// marked below, and until the part arrives the build uses a stub that compiles,
// runs, and receives nothing — so everything above it can be flashed and
// watched today.
#include <Arduino.h>
#include "ac_gateway.h"
#include "ac_esp.h"

using namespace ac;

static const uint32_t kGatewayId  = 0xAA000001;
static const uint32_t kBufferRecs = 4000;      // about 11 hours of three nodes
static const int      kPinButton  = 0;         // BOOT — stands in for the uplink
static const uint32_t kForwardMs  = 2000;

// ── the LoRa side ────────────────────────────────────────────────────────
//
// TODO(hardware): replace with RadioLib and the SX1262 once the module is on
// the bench. Wiring for the ESP32-S3: NSS 10, DIO1 14, NRST 15, BUSY 16,
// SCK 12, MISO 13, MOSI 11. Region IN865 (865–867 MHz) — not 433, not 915;
// using the wrong band here is both illegal and untestable in India.
//
//   #include <RadioLib.h>
//   static SX1262 radio = new Module(10, 14, 15, 16);
//   begin():   radio.begin(866.0, 125.0, 9, 7, 0x34, 22) == RADIOLIB_ERR_NONE
//              then radio.startReceive()
//   receive(): radio.readData(buf, kRecBytes) == RADIOLIB_ERR_NONE
//              rssi = (int16_t)radio.getRSSI()
//   ack():     a short downlink frame, or piggy-backed on the next beacon
//
// Everything above this class is already tested — see tools/selftest.cpp,
// "The truck gateway collects from three nodes".
class LoraRadio : public IGatewayRadio {
 public:
  bool begin() override {
    Serial.println("# LoRa: SX1262 driver not fitted yet (stub)");
    return true;
  }
  bool receive(uint8_t rec[kRecBytes], int16_t& rssi) override {
    (void)rec; (void)rssi;
    return false;                 // nothing arrives until the module is here
  }
  bool ack(uint32_t device, uint32_t seq) override {
    (void)device; (void)seq;
    return true;
  }
};

static ArduinoClock clk(kClockBase);             // 25 Sep 2026
static LoraRadio    lora;
static GwBuffer     buffer(kBufferRecs);
static SerialLink   uplink(Serial);      // USB stands in for Wi-Fi / 4G
static Gateway      gw(kGatewayId, clk, lora, buffer, uplink);

static uint32_t lastForward = 0;
static bool     lastButton  = true;

static void banner() {
  const GwStats& s = gw.stats();
  Serial.printf("\n# AnnaChain gateway %08X\n", kGatewayId);
  Serial.printf("# buffer %u records · holding %u · dropped %u\n",
                buffer.capacity(), gw.buffered(), s.dropped);
  Serial.printf("# heard %u records from %u nodes · forwarded %u · duplicates %u\n",
                s.received, s.nodes, s.forwarded, s.duplicates);
  Serial.println("# press BOOT to drop the uplink, press again to restore it");
}

void setup() {
  Serial.begin(115200);
  delay(400);
  pinMode(kPinButton, INPUT_PULLUP);

  if (!gw.begin()) {
    Serial.println("# GATEWAY FAILED TO START — halting");
    while (true) delay(1000);
  }
  gw.setBatchSize(20);
  banner();
}

void loop() {
  bool b = digitalRead(kPinButton);
  if (lastButton && !b) {
    uplink.setUp(!uplink.up());
    Serial.printf("\n# UPLINK %s  (holding %u)\n",
                  uplink.up() ? "UP — draining the buffer" : "DOWN — still collecting",
                  gw.buffered());
    delay(200);
  }
  lastButton = b;

  if (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd == "stat") banner();
  }

  gw.poll();                       // take whatever LoRa has heard

  if (millis() - lastForward >= kForwardMs) {
    lastForward = millis();
    uint32_t before = gw.stats().forwarded;
    gw.forward();
    uint32_t sent = gw.stats().forwarded - before;
    if (sent) Serial.printf("# forwarded %u · holding %u\n", sent, gw.buffered());
  }
}
