// AnnaChain — gateway firmware for the ESP32-S3 in the truck cab.
//
//   AC_WIFI_SSID=... AC_WIFI_PASS=... AC_SERVER_URL=http://192.168.1.14:8000 \
//     pio run -e gateway -t upload
//
// One of these per truck. Every crate node sends to it over LoRa; it buffers
// what it hears and pushes it upstream whenever there is a signal.
//
// It holds no signing key and never will. Records cross it byte for byte, are
// verified at the server against the node's own signature, and a gateway that
// is tampered with can therefore delay or lose data but cannot invent it.
// Gap notices cross it the same way: queued with the records, and sent up
// exactly as the node signed them.
//
// The drivers under this file are UNPROVEN: the SX1262 (lib/ac/ac_lora.*), and
// Wi-Fi + NTP (lib/ac/ac_net.*). They compile. None has run on a board, because
// no board has arrived. docs/HIL.md steps 5 and 6 are how that changes.
//
// Uplink: Wi-Fi to the server's HTTP API when AC_WIFI_SSID was set at build
// time, otherwise the USB serial line and backend/bridge_serial.py, as before.
// Pins: lib/ac/ac_pins.h, each one justified there.
#include <Arduino.h>
#include <WiFi.h>
#include "ac_gateway.h"
#include "ac_esp.h"
#include "ac_lora.h"
#include "ac_net.h"
#include "ac_pins.h"

using namespace ac;

#ifndef AC_WIFI_SSID
#define AC_WIFI_SSID ""
#endif
#ifndef AC_WIFI_PASS
#define AC_WIFI_PASS ""
#endif
#ifndef AC_SERVER_URL
#define AC_SERVER_URL ""
#endif

static const uint32_t kGatewayId  = 0xAA000001;
static const uint32_t kBufferRecs = 4000;      // about 11 hours of three nodes
static const uint32_t kForwardMs  = 2000;

static Sx1262Transport radioHw(pins::kLoraNss, pins::kLoraDio1, pins::kLoraReset,
                               pins::kLoraBusy, pins::kLoraSck, pins::kLoraMiso,
                               pins::kLoraMosi);
static LoraRadio    lora(radioHw);
static NtpClock     clk(kClockBase);            // compiled-in date until NTP answers
static GwBuffer     buffer(kBufferRecs);
static WifiHttpLink wifiUp(AC_SERVER_URL);
static SerialLink   serialUp(Serial);           // the bench fallback
static const bool   kUseWifi = sizeof(AC_WIFI_SSID) > 1 && sizeof(AC_SERVER_URL) > 1;
static ILink&       uplink = kUseWifi ? (ILink&)wifiUp : (ILink&)serialUp;
static Gateway      gw(kGatewayId, clk, lora, buffer, uplink);

static uint32_t lastForward = 0;
static bool     lastButton  = true;

static void setUplink(bool u) {
  if (kUseWifi) wifiUp.setUp(u); else serialUp.setUp(u);
}

static void banner() {
  const GwStats& s = gw.stats();
  Serial.printf("\n# AnnaChain gateway %08X\n", kGatewayId);
  Serial.printf("# uplink: %s  ·  clock: %s\n",
                kUseWifi ? "Wi-Fi HTTP (UNPROVEN)" : "USB serial",
                clk.synced() ? "NTP" : "compiled-in date (not synced)");
  Serial.printf("# LoRa: SX1262 %s (UNPROVEN driver)\n", radioHw.ready() ? "up" : "NOT READY");
  Serial.printf("# buffer %u frames · holding %u · dropped %u records, %u gap notices\n",
                buffer.capacity(), gw.buffered(), s.dropped, s.gapsDropped);
  Serial.printf("# gap notices: heard %u · forwarded %u\n",
                s.gapsReceived, s.gapsForwarded);
  Serial.printf("# heard %u records from %u nodes · forwarded %u · duplicates %u\n",
                s.received, s.nodes, s.forwarded, s.duplicates);
  Serial.println("# press BOOT to drop the uplink, press again to restore it");
}

void setup() {
  Serial.begin(115200);
  delay(400);
  pinMode(pins::kButton, INPUT_PULLUP);

  if (kUseWifi) {
    if (wifiConnect(AC_WIFI_SSID, AC_WIFI_PASS)) {
      Serial.printf("# Wi-Fi up, %s\n", WiFi.localIP().toString().c_str());
      if (!clk.sync()) Serial.println("# NTP did not answer; using the compiled-in date");
    } else {
      Serial.println("# Wi-Fi did not connect; will keep buffering");
    }
  }

  if (!gw.begin()) {
    // The buffer allocated, but the radio did not start. Keep running: the
    // banner says NOT READY, and the smoke test says why.
    Serial.println("# SX1262 did not start — see the smoke test (pio run -e smoke)");
  }
  gw.setBatchSize(20);
  banner();
}

void loop() {
  bool b = digitalRead(pins::kButton);
  if (lastButton && !b) {
    setUplink(!uplink.up());
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

  // Wi-Fi drops in a moving truck. Try again now and then; the buffer holds.
  // An unsynced clock is retried on the same slow beat, never every loop.
  static uint32_t lastRetry = 0;
  if (kUseWifi && millis() - lastRetry > 30000) {
    lastRetry = millis();
    if (WiFi.status() != WL_CONNECTED) WiFi.reconnect();
    else if (!clk.synced()) clk.sync(2000);
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
