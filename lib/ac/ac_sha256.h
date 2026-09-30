// AnnaChain — SHA-256 (FIPS 180-4) and HMAC-SHA256.
// Plain C++, no dependencies, so the same code runs on the laptop and on the ESP32.
#pragma once
#include <stddef.h>
#include <stdint.h>

namespace ac {

class Sha256 {
 public:
  Sha256() { reset(); }
  void reset();
  void update(const uint8_t* data, size_t len);
  void finish(uint8_t out[32]);

  static void hash(const uint8_t* data, size_t len, uint8_t out[32]);

 private:
  void block(const uint8_t* p);
  uint32_t h_[8];
  uint8_t  buf_[64];
  size_t   buflen_;
  uint64_t total_;
};

// HMAC-SHA256. Stands in for the ATECC608B's ECDSA signature until the secure
// element is on the board; the call site does not change when it is.
void hmac_sha256(const uint8_t* key, size_t keylen,
                 const uint8_t* msg, size_t msglen,
                 uint8_t out[32]);

void hex32(const uint8_t in[32], char out[65]);

}  // namespace ac
