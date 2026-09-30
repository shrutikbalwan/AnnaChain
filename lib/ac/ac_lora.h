// AnnaChain — the SX1262 LoRa radio, both ends of it.
//
// ╔══════════════════════════════════════════════════════════════════════════╗
// ║ UNPROVEN. This compiles against RadioLib 7.8.1 (envs node_lora, gateway, ║
// ║ smoke, 30 Sep 2026). It has never keyed an SX1262: none has arrived.     ║
// ║ Range, packet loss, timing and the TCXO/RF-switch settings below are all ║
// ║ unmeasured. docs/HIL.md steps 5 and 6.                                   ║
// ╚══════════════════════════════════════════════════════════════════════════╝
//
// Band: IN865, 865–867 MHz — not 433, not 915. The carrier defaults to
// 865.0625 MHz, the first default channel of the LoRaWAN IN865 regional plan.
// Check the permitted transmit power for your deployment before raising
// AC_LORA_DBM above its conservative default.
//
// On air, every packet starts with a magic byte and a kind:
//   0xAC FRAME_RECORD  + 84 bytes   a record, exactly as signed by the node
//   0xAC FRAME_GAP     + 84 bytes   a signed gap notice (ac_record.h)
//   0xAC FRAME_ACK     + device 4 + seq 4    gateway -> node, hop-by-hop
//   0xAC FRAME_QUERY   + device 4            node -> gateway: what does the server have?
//   0xAC FRAME_LASTACK + device 4 + known 1 + seq 4
//                                            gateway -> node: the server's last-ACK,
//                                            or known = 0 for "no value" (ac_gateway.h)
// The radio carries bytes; it never interprets a record. Same rule as the
// gateway: a compromised hop can delay or drop, not invent.
//
// Module options that differ between SX1262 boards, set with build flags:
//   AC_LORA_TCXO_V   TCXO voltage on DIO3 (default 1.8). 0 for a crystal module.
//   AC_LORA_DIO2_RFSW  1 (default) if DIO2 drives the antenna switch.
// Getting either wrong is the classic "begin() fails" or "transmits but never
// receives" symptom; see the troubleshooting table in docs/BRINGUP.md.
#pragma once
#ifdef ARDUINO
#include <Arduino.h>
#include <SPI.h>
#include "ac_hal.h"
#include "ac_gateway.h"

#ifndef AC_LORA_FREQ_MHZ
#define AC_LORA_FREQ_MHZ 865.0625f
#endif
#ifndef AC_LORA_DBM
#define AC_LORA_DBM 14
#endif
#ifndef AC_LORA_TCXO_V
#define AC_LORA_TCXO_V 1.8f
#endif
#ifndef AC_LORA_DIO2_RFSW
#define AC_LORA_DIO2_RFSW 1
#endif

namespace ac {

enum : uint8_t { kLoraMagic = 0xAC, FRAME_ACK = 0x80, FRAME_LASTACK = 0x81 };

// The radio itself: bring-up, send a packet, poll for one.
class Sx1262Transport {
 public:
  Sx1262Transport(int nss, int dio1, int rst, int busy, int sck, int miso, int mosi)
      : nss_(nss), dio1_(dio1), rst_(rst), busy_(busy), sck_(sck), miso_(miso), mosi_(mosi) {}

  // Returns RadioLib's status code: 0 is success, negative is RadioLib's error.
  int16_t begin();
  bool ready() const { return ready_; }

  bool transmit(uint8_t kind, const uint8_t* payload, size_t len);
  // Non-blocking. Fills kind and payload (up to cap bytes); returns the payload
  // length, or -1 when nothing complete has arrived.
  int  poll(uint8_t& kind, uint8_t* payload, size_t cap, int16_t& rssi);

  // The chip's version string (register 0x0320), for the smoke test.
  bool versionString(char out[17]);

 private:
  int nss_, dio1_, rst_, busy_, sck_, miso_, mosi_;
  bool ready_ = false;
};

// Gateway side: the IGatewayRadio the Gateway class already polls.
class LoraRadio : public IGatewayRadio {
 public:
  explicit LoraRadio(Sx1262Transport& t) : t_(t) {}
  bool begin() override { return t_.begin() == 0; }
  bool receive(uint8_t frame[kRecBytes], uint8_t& kind, int16_t& rssi) override;
  bool ack(uint32_t device, uint32_t seq) override;
  bool lastAck(uint32_t device, bool known, uint32_t seq) override;
 private:
  Sx1262Transport& t_;
};

// Node side: an ILink that goes to the gateway over LoRa, not to the server.
// Acknowledgements of records are hop-by-hop ("the gateway has it"), but
// queryLastAck() asks the gateway for the SERVER's last-ACK, which the gateway
// relays (ac_gateway.h), so a lost gateway buffer is resent from the right place.
class LoraNodeLink : public ILink {
 public:
  explicit LoraNodeLink(Sx1262Transport& t, uint32_t ackTimeoutMs = 1500, int tries = 3)
      : t_(t), timeout_(ackTimeoutMs), tries_(tries) {}
  bool up() override { return up_ && t_.ready(); }
  bool queryLastAck(uint32_t device, uint32_t& lastAck) override;
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override;
  bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override;
  void setUp(bool u) { up_ = u; }
 private:
  bool sendFrame(uint8_t kind, const uint8_t frame[kRecBytes], uint32_t device, uint32_t seq);
  Sx1262Transport& t_;
  uint32_t timeout_;
  int tries_;
  bool up_ = true;
  uint32_t ack_ = 0;
};

}  // namespace ac
#endif  // ARDUINO
