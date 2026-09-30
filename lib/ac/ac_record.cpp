#include "ac_record.h"
#include "ac_sha256.h"
#include <stdio.h>
#include <string.h>

namespace ac {

static inline void put32(uint8_t*& p, uint32_t v) {
  *p++ = (uint8_t)(v);       *p++ = (uint8_t)(v >> 8);
  *p++ = (uint8_t)(v >> 16); *p++ = (uint8_t)(v >> 24);
}
static inline void put16(uint8_t*& p, uint16_t v) {
  *p++ = (uint8_t)(v); *p++ = (uint8_t)(v >> 8);
}
static inline uint32_t get32(const uint8_t*& p) {
  uint32_t v = (uint32_t)p[0] | (uint32_t)p[1] << 8 |
               (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
  p += 4; return v;
}
static inline uint16_t get16(const uint8_t*& p) {
  uint16_t v = (uint16_t)(p[0] | (uint16_t)p[1] << 8); p += 2; return v;
}

uint8_t recordFormat(const uint8_t* raw, size_t len) {
  (void)raw;                 // v1 carries no version byte; its length is its version
  return len == kRecBytes ? kFormatV1 : 0;
}

void encodeBody(const Record& r, uint8_t out[kBodyBytes]) {
  uint8_t* p = out;
  put32(p, r.device);
  put32(p, r.seq);
  put32(p, r.ts);
  put16(p, (uint16_t)r.temp);
  put16(p, r.rh);
  put16(p, r.c2h4);
  *p++ = r.flags;
  *p++ = r.batt;
  memcpy(p, r.prev, 32); p += 32;
}

void encode(const Record& r, uint8_t out[kRecBytes]) {
  encodeBody(r, out);
  memcpy(out + kBodyBytes, r.sig, 32);
}

bool decode(const uint8_t in[kRecBytes], Record& out) {
  const uint8_t* p = in;
  out.device = get32(p);
  out.seq    = get32(p);
  out.ts     = get32(p);
  out.temp   = (int16_t)get16(p);
  out.rh     = get16(p);
  out.c2h4   = get16(p);
  out.flags  = *p++;
  out.batt   = *p++;
  memcpy(out.prev, p, 32); p += 32;
  memcpy(out.sig,  p, 32);
  return true;
}

void digest(const Record& r, uint8_t out[32]) {
  uint8_t body[kBodyBytes];
  encodeBody(r, body);
  Sha256::hash(body, kBodyBytes, out);
}

// "ACGAP|<device>|<from>|<to>", decimal ASCII, no padding. The server builds
// the same string, so the two must agree character for character.
void gapDigest(uint32_t device, uint32_t from, uint32_t to, uint8_t out[32]) {
  char msg[64];
  int n = snprintf(msg, sizeof(msg), "ACGAP|%lu|%lu|%lu", (unsigned long)device,
                   (unsigned long)from, (unsigned long)to);
  Sha256::hash((const uint8_t*)msg, (size_t)n, out);
}

void encodeGapFrame(uint32_t device, uint32_t from, uint32_t to,
                    const uint8_t mac[32], uint8_t out[kRecBytes]) {
  memset(out, 0, kRecBytes);
  uint8_t* p = out;
  put32(p, device); put32(p, from); put32(p, to);
  memcpy(p, mac, 32);
}

void decodeGapFrame(const uint8_t in[kRecBytes], uint32_t& device, uint32_t& from,
                    uint32_t& to, uint8_t mac[32]) {
  const uint8_t* p = in;
  device = get32(p); from = get32(p); to = get32(p);
  memcpy(mac, p, 32);
}

}  // namespace ac
