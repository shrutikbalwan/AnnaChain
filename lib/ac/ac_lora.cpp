// UNPROVEN — compiles (envs node_lora, gateway, smoke); never run against an SX1262.
// See ac_lora.h.
#ifdef ARDUINO
#include "ac_lora.h"

#if __has_include(<RadioLib.h>)
#include <RadioLib.h>

namespace ac {

static SPIClass  g_spi(FSPI);
static SX1262*   g_radio = nullptr;
static volatile bool g_rxFlag = false;

// DIO1 fires on RxDone / TxDone. Only set a flag here; everything else happens
// in poll(), outside the interrupt.
static void IRAM_ATTR onDio1() { g_rxFlag = true; }

static inline void put32(uint8_t* p, uint32_t v) {
  p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); p[2] = (uint8_t)(v >> 16); p[3] = (uint8_t)(v >> 24);
}
static inline uint32_t get32(const uint8_t* p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

int16_t Sx1262Transport::begin() {
  ready_ = false;
  g_spi.begin(sck_, miso_, mosi_, nss_);
  if (!g_radio) g_radio = new SX1262(new Module(nss_, dio1_, rst_, busy_, g_spi));

  // 125 kHz, SF9, CR 4/7, private sync word, 8-symbol preamble. SF9 is a
  // middle choice for a trailer: range over speed, unmeasured.
  int16_t rc = g_radio->begin(AC_LORA_FREQ_MHZ, 125.0f, 9, 7,
                              RADIOLIB_SX126X_SYNC_WORD_PRIVATE, AC_LORA_DBM, 8,
                              AC_LORA_TCXO_V);
  if (rc != RADIOLIB_ERR_NONE) return rc;
#if AC_LORA_DIO2_RFSW
  rc = g_radio->setDio2AsRfSwitch(true);
  if (rc != RADIOLIB_ERR_NONE) return rc;
#endif
  g_radio->setDio1Action(onDio1);
  g_rxFlag = false;
  rc = g_radio->startReceive();
  if (rc != RADIOLIB_ERR_NONE) return rc;
  ready_ = true;
  return 0;
}

bool Sx1262Transport::transmit(uint8_t kind, const uint8_t* payload, size_t len) {
  if (!ready_ || len > 253) return false;
  uint8_t pkt[255];
  pkt[0] = kLoraMagic; pkt[1] = kind;
  memcpy(pkt + 2, payload, len);
  int16_t rc = g_radio->transmit(pkt, len + 2);   // blocking; DIO1 fires on TxDone
  g_rxFlag = false;                               // ...which is not a received packet
  g_radio->startReceive();                        // half duplex: listen again
  return rc == RADIOLIB_ERR_NONE;
}

int Sx1262Transport::poll(uint8_t& kind, uint8_t* payload, size_t cap, int16_t& rssi) {
  if (!ready_ || !g_rxFlag) return -1;
  g_rxFlag = false;
  uint8_t pkt[255];
  size_t n = g_radio->getPacketLength();
  int16_t rc = (n >= 2 && n <= sizeof(pkt)) ? g_radio->readData(pkt, n) : RADIOLIB_ERR_PACKET_TOO_LONG;
  rssi = (int16_t)g_radio->getRSSI();
  g_radio->startReceive();
  if (rc != RADIOLIB_ERR_NONE || pkt[0] != kLoraMagic || n - 2 > cap) return -1;  // CRC fail, noise, or not ours
  kind = pkt[1];
  memcpy(payload, pkt + 2, n - 2);
  return (int)(n - 2);
}

bool Sx1262Transport::versionString(char out[17]) {
  // Straight SPI, not RadioLib: the smoke test wants an answer even when
  // RadioLib's begin() fails, since that failure is what it is diagnosing.
  // SX1262 ReadRegister (opcode 0x1D): opcode, address high, address low, one
  // status byte, then data. The version string lives at register 0x0320
  // (RadioLib: RADIOLIB_SX126X_REG_VERSION_STRING).
  out[0] = 0;
  if (ready_) return false;           // the radio is in use; do not poke it
  pinMode(nss_, OUTPUT); digitalWrite(nss_, HIGH);
  pinMode(busy_, INPUT);
  pinMode(rst_, OUTPUT);
  digitalWrite(rst_, LOW); delay(2); digitalWrite(rst_, HIGH);
  uint32_t t0 = millis();
  while (digitalRead(busy_) == HIGH) {             // BUSY low = ready for a command
    if (millis() - t0 > 200) return false;         // stuck high: wiring or power
    delay(1);
  }
  g_spi.begin(sck_, miso_, mosi_, nss_);
  uint8_t buf[16];
  g_spi.beginTransaction(SPISettings(2000000, MSBFIRST, SPI_MODE0));
  digitalWrite(nss_, LOW);
  g_spi.transfer(0x1D);
  g_spi.transfer((uint8_t)(RADIOLIB_SX126X_REG_VERSION_STRING >> 8));
  g_spi.transfer((uint8_t)(RADIOLIB_SX126X_REG_VERSION_STRING & 0xFF));
  g_spi.transfer(0x00);                            // status
  for (int i = 0; i < 16; ++i) buf[i] = g_spi.transfer(0x00);
  digitalWrite(nss_, HIGH);
  g_spi.endTransaction();
  bool printable = false;
  for (int i = 0; i < 16; ++i) {
    out[i] = (buf[i] >= 32 && buf[i] < 127) ? (char)buf[i] : '.';
    if (buf[i] >= 'A' && buf[i] <= 'Z') printable = true;
  }
  out[16] = 0;
  return printable;                                // all 0x00 or 0xFF = nothing answered
}

// ── gateway side ───────────────────────────────────────────────────────────
bool LoraRadio::receive(uint8_t frame[kRecBytes], uint8_t& kind, int16_t& rssi) {
  uint8_t k; uint8_t buf[kRecBytes];
  for (;;) {
    int n = t_.poll(k, buf, sizeof(buf), rssi);
    if (n < 0) return false;
    if ((k == FRAME_RECORD || k == FRAME_GAP) && n == (int)kRecBytes) {
      memcpy(frame, buf, kRecBytes);
      kind = k;
      return true;
    }
    if (k == FRAME_QUERY && n >= 4) {           // device 4: padded to a frame
      memset(frame, 0, kRecBytes);
      memcpy(frame, buf, 4);
      kind = k;
      return true;
    }
    // anything else (another gateway's ack, a stray frame) is ignored
  }
}

bool LoraRadio::ack(uint32_t device, uint32_t seq) {
  uint8_t p[8];
  put32(p, device); put32(p + 4, seq);
  return t_.transmit(FRAME_ACK, p, sizeof(p));
}

bool LoraRadio::lastAck(uint32_t device, bool known, uint32_t seq) {
  uint8_t p[9];
  put32(p, device); p[4] = known ? 1 : 0; put32(p + 5, seq);
  return t_.transmit(FRAME_LASTACK, p, sizeof(p));
}

// ── node side ──────────────────────────────────────────────────────────────
bool LoraNodeLink::sendFrame(uint8_t kind, const uint8_t frame[kRecBytes],
                             uint32_t device, uint32_t seq) {
  for (int attempt = 0; attempt < tries_; ++attempt) {
    if (!t_.transmit(kind, frame, kRecBytes)) return false;
    uint32_t t0 = millis();
    while (millis() - t0 < timeout_) {
      uint8_t k; uint8_t p[16]; int16_t rssi;
      int n = t_.poll(k, p, sizeof(p), rssi);
      if (n == 8 && k == FRAME_ACK && get32(p) == device && get32(p + 4) == seq) return true;
      delay(2);
    }
  }
  return false;               // no ack: treat as no link, keep everything, retry later
}

bool LoraNodeLink::queryLastAck(uint32_t device, uint32_t& lastAck) {
  // Ask the gateway what the SERVER holds. "No value" (the gateway has no
  // uplink, or the server did not answer) and no answer at all both return
  // false: the node keeps what it last knew. Never ack_, which only says the
  // gateway once had it, and never 0, which would resend the whole flash.
  if (!up()) return false;
  uint8_t q[4];
  put32(q, device);
  for (int attempt = 0; attempt < tries_; ++attempt) {
    if (!t_.transmit(FRAME_QUERY, q, sizeof(q))) return false;
    uint32_t t0 = millis();
    while (millis() - t0 < timeout_) {
      uint8_t k; uint8_t p[16]; int16_t rssi;
      int n = t_.poll(k, p, sizeof(p), rssi);
      if (n == 9 && k == FRAME_LASTACK && get32(p) == device) {
        if (!p[4]) return false;                 // the gateway has no value
        lastAck = get32(p + 5);
        return true;
      }
      delay(2);
    }
  }
  return false;
}

bool LoraNodeLink::send(const uint8_t* recs, size_t count, uint32_t& acked) {
  if (!up()) return false;
  for (size_t i = 0; i < count; ++i) {
    const uint8_t* r = recs + i * kRecBytes;
    uint32_t device = get32(r), seq = get32(r + 4);
    if (!sendFrame(FRAME_RECORD, r, device, seq)) { acked = ack_; return i > 0; }
    ack_ = seq;
  }
  acked = ack_;
  return true;
}

bool LoraNodeLink::declareGap(uint32_t device, uint32_t from, uint32_t to,
                              const uint8_t mac[32]) {
  if (!up()) return false;
  uint8_t frame[kRecBytes];
  encodeGapFrame(device, from, to, mac, frame);
  if (!sendFrame(FRAME_GAP, frame, device, to)) return false;
  ack_ = to;
  return true;
}

}  // namespace ac

#else   // built without RadioLib: nothing here is linked in a meaningful way
namespace ac {
int16_t Sx1262Transport::begin() { return -1; }
bool Sx1262Transport::transmit(uint8_t, const uint8_t*, size_t) { return false; }
int  Sx1262Transport::poll(uint8_t&, uint8_t*, size_t, int16_t&) { return -1; }
bool Sx1262Transport::versionString(char out[17]) { out[0] = 0; return false; }
bool LoraRadio::receive(uint8_t*, uint8_t&, int16_t&) { return false; }
bool LoraRadio::ack(uint32_t, uint32_t) { return false; }
bool LoraRadio::lastAck(uint32_t, bool, uint32_t) { return false; }
bool LoraNodeLink::queryLastAck(uint32_t, uint32_t&) { return false; }
bool LoraNodeLink::send(const uint8_t*, size_t, uint32_t&) { return false; }
bool LoraNodeLink::declareGap(uint32_t, uint32_t, uint32_t, const uint8_t*) { return false; }
}  // namespace ac
#endif
#endif  // ARDUINO
