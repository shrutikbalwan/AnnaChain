// UNPROVEN — compiles (env gateway); never run on a network from a board. See ac_net.h.
#ifdef ARDUINO
#include "ac_net.h"
#include "ac_sha256.h"
#include <WiFi.h>
#include <HTTPClient.h>
#include <time.h>
#include <esp_timer.h>

namespace ac {

bool wifiConnect(const char* ssid, const char* pass, uint32_t timeoutMs) {
  if (!ssid || !*ssid) return false;
  WiFi.mode(WIFI_STA);
  WiFi.begin(ssid, pass);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < timeoutMs) delay(100);
  return WiFi.status() == WL_CONNECTED;
}

// ── NtpClock ───────────────────────────────────────────────────────────────
bool NtpClock::sync(uint32_t timeoutMs) {
  if (WiFi.status() != WL_CONNECTED) return false;
  configTime(0, 0, "pool.ntp.org", "time.google.com");   // UTC; records are UTC
  uint32_t t0 = millis();
  while (millis() - t0 < timeoutMs) {
    // Anything earlier than the build time (kClockBase) is the clock's power-on
    // value, not an answer.
    if ((uint32_t)time(nullptr) >= kClockBase) { synced_ = true; return true; }
    delay(100);
  }
  return false;
}

uint32_t NtpClock::now() {
  if (synced_) return (uint32_t)time(nullptr);   // SNTP keeps adjusting it
  // esp_timer, not millis(): millis() wraps after 49.7 days (ac_esp.h).
  return base_ + (uint32_t)(esp_timer_get_time() / 1000000LL);
}

// ── WifiHttpLink ───────────────────────────────────────────────────────────
static long jsonNumber(const String& body, const char* key) {
  // Just enough JSON: find "key": and read the integer after it.
  String k = String("\"") + key + "\"";
  int i = body.indexOf(k);
  if (i < 0) return -1;
  i = body.indexOf(':', i + k.length());
  if (i < 0) return -1;
  return body.substring(i + 1).toInt();
}

bool WifiHttpLink::up() {
  return enabled_ && base_.length() && WiFi.status() == WL_CONNECTED;
}

bool WifiHttpLink::queryLastAck(uint32_t device, uint32_t& lastAck) {
  time_ = 0;
  if (!up()) return false;
  HTTPClient http;
  http.setTimeout(5000);
  if (!http.begin(base_ + "/api/lastack/" + String(device))) return false;
  status_ = http.GET();
  if (status_ == 404) { http.end(); lastAck = 0; return true; }   // not enrolled yet
  if (status_ != 200) { http.end(); return false; }
  String body = http.getString();
  http.end();
  long v = jsonNumber(body, "last_ack");
  if (v < 0) return false;
  lastAck = (uint32_t)v;
  long t = jsonNumber(body, "now");       // absent from an older server: no time
  time_ = t > 0 ? (uint32_t)t : 0;
  return true;
}

bool WifiHttpLink::send(const uint8_t* recs, size_t count, uint32_t& acked) {
  if (!up() || !count) return false;
  static const char* hx = "0123456789abcdef";
  String body;
  body.reserve(16 + count * (kRecBytes * 2 + 3));
  body += "{\"records\":[";
  for (size_t i = 0; i < count; ++i) {
    if (i) body += ',';
    body += '"';
    const uint8_t* r = recs + i * kRecBytes;
    for (size_t b = 0; b < kRecBytes; ++b) { body += hx[r[b] >> 4]; body += hx[r[b] & 15]; }
    body += '"';
  }
  body += "]}";

  HTTPClient http;
  http.setTimeout(10000);
  if (!http.begin(base_ + "/api/ingest")) return false;
  http.addHeader("Content-Type", "application/json");
  status_ = http.POST(body);
  if (status_ != 200) { http.end(); return false; }   // keep everything, retry later
  long v = jsonNumber(http.getString(), "last_ack");
  http.end();
  acked = v < 0 ? 0 : (uint32_t)v;
  return true;          // the link worked; whether the server took them is in acked
}

bool WifiHttpLink::declareGap(uint32_t device, uint32_t from, uint32_t to,
                              const uint8_t mac[32]) {
  if (!up()) return false;
  char hex[65]; hex32(mac, hex);
  String body = String("{\"device\":") + device + ",\"from_seq\":" + from +
                ",\"to_seq\":" + to + ",\"mac\":\"" + hex + "\"}";
  HTTPClient http;
  http.setTimeout(5000);
  if (!http.begin(base_ + "/api/gap")) return false;
  http.addHeader("Content-Type", "application/json");
  status_ = http.POST(body);
  http.end();
  return status_ == 200;   // 401 (bad signature) or 409 (out of place) is a refusal
}

}  // namespace ac
#endif  // ARDUINO
