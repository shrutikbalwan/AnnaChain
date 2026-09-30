#include "ac_sha256.h"
#include <string.h>

namespace ac {

static const uint32_t K[64] = {
  0x428a2f98u,0x71374491u,0xb5c0fbcfu,0xe9b5dba5u,0x3956c25bu,0x59f111f1u,
  0x923f82a4u,0xab1c5ed5u,0xd807aa98u,0x12835b01u,0x243185beu,0x550c7dc3u,
  0x72be5d74u,0x80deb1feu,0x9bdc06a7u,0xc19bf174u,0xe49b69c1u,0xefbe4786u,
  0x0fc19dc6u,0x240ca1ccu,0x2de92c6fu,0x4a7484aau,0x5cb0a9dcu,0x76f988dau,
  0x983e5152u,0xa831c66du,0xb00327c8u,0xbf597fc7u,0xc6e00bf3u,0xd5a79147u,
  0x06ca6351u,0x14292967u,0x27b70a85u,0x2e1b2138u,0x4d2c6dfcu,0x53380d13u,
  0x650a7354u,0x766a0abbu,0x81c2c92eu,0x92722c85u,0xa2bfe8a1u,0xa81a664bu,
  0xc24b8b70u,0xc76c51a3u,0xd192e819u,0xd6990624u,0xf40e3585u,0x106aa070u,
  0x19a4c116u,0x1e376c08u,0x2748774cu,0x34b0bcb5u,0x391c0cb3u,0x4ed8aa4au,
  0x5b9cca4fu,0x682e6ff3u,0x748f82eeu,0x78a5636fu,0x84c87814u,0x8cc70208u,
  0x90befffau,0xa4506cebu,0xbef9a3f7u,0xc67178f2u
};

static inline uint32_t ror(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

void Sha256::reset() {
  h_[0]=0x6a09e667u; h_[1]=0xbb67ae85u; h_[2]=0x3c6ef372u; h_[3]=0xa54ff53au;
  h_[4]=0x510e527fu; h_[5]=0x9b05688cu; h_[6]=0x1f83d9abu; h_[7]=0x5be0cd19u;
  buflen_ = 0; total_ = 0;
}

void Sha256::block(const uint8_t* p) {
  uint32_t w[64];
  for (int i = 0; i < 16; ++i)
    w[i] = (uint32_t)p[i*4] << 24 | (uint32_t)p[i*4+1] << 16 |
           (uint32_t)p[i*4+2] << 8 | (uint32_t)p[i*4+3];
  for (int i = 16; i < 64; ++i) {
    uint32_t s0 = ror(w[i-15],7) ^ ror(w[i-15],18) ^ (w[i-15] >> 3);
    uint32_t s1 = ror(w[i-2],17) ^ ror(w[i-2],19) ^ (w[i-2] >> 10);
    w[i] = w[i-16] + s0 + w[i-7] + s1;
  }
  uint32_t a=h_[0],b=h_[1],c=h_[2],d=h_[3],e=h_[4],f=h_[5],g=h_[6],h=h_[7];
  for (int i = 0; i < 64; ++i) {
    uint32_t S1 = ror(e,6) ^ ror(e,11) ^ ror(e,25);
    uint32_t ch = (e & f) ^ (~e & g);
    uint32_t t1 = h + S1 + ch + K[i] + w[i];
    uint32_t S0 = ror(a,2) ^ ror(a,13) ^ ror(a,22);
    uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
    uint32_t t2 = S0 + maj;
    h=g; g=f; f=e; e=d+t1; d=c; c=b; b=a; a=t1+t2;
  }
  h_[0]+=a; h_[1]+=b; h_[2]+=c; h_[3]+=d; h_[4]+=e; h_[5]+=f; h_[6]+=g; h_[7]+=h;
}

void Sha256::update(const uint8_t* data, size_t len) {
  total_ += len;
  while (len) {
    size_t n = 64 - buflen_;
    if (n > len) n = len;
    memcpy(buf_ + buflen_, data, n);
    buflen_ += n; data += n; len -= n;
    if (buflen_ == 64) { block(buf_); buflen_ = 0; }
  }
}

void Sha256::finish(uint8_t out[32]) {
  uint64_t bits = total_ * 8;
  uint8_t pad = 0x80;
  update(&pad, 1);
  uint8_t zero = 0x00;
  while (buflen_ != 56) update(&zero, 1);
  uint8_t len[8];
  for (int i = 0; i < 8; ++i) len[i] = (uint8_t)(bits >> (56 - 8*i));
  update(len, 8);
  for (int i = 0; i < 8; ++i) {
    out[i*4]   = (uint8_t)(h_[i] >> 24);
    out[i*4+1] = (uint8_t)(h_[i] >> 16);
    out[i*4+2] = (uint8_t)(h_[i] >> 8);
    out[i*4+3] = (uint8_t)(h_[i]);
  }
}

void Sha256::hash(const uint8_t* data, size_t len, uint8_t out[32]) {
  Sha256 s; s.update(data, len); s.finish(out);
}

void hmac_sha256(const uint8_t* key, size_t keylen,
                 const uint8_t* msg, size_t msglen, uint8_t out[32]) {
  uint8_t k[64]; memset(k, 0, sizeof(k));
  if (keylen > 64) Sha256::hash(key, keylen, k);
  else             memcpy(k, key, keylen);

  uint8_t ipad[64], opad[64];
  for (int i = 0; i < 64; ++i) { ipad[i] = k[i] ^ 0x36; opad[i] = k[i] ^ 0x5c; }

  uint8_t inner[32];
  { Sha256 s; s.update(ipad, 64); s.update(msg, msglen); s.finish(inner); }
  { Sha256 s; s.update(opad, 64); s.update(inner, 32); s.finish(out); }
}

void hex32(const uint8_t in[32], char out[65]) {
  static const char* d = "0123456789abcdef";
  for (int i = 0; i < 32; ++i) { out[i*2] = d[in[i] >> 4]; out[i*2+1] = d[in[i] & 15]; }
  out[64] = 0;
}

}  // namespace ac
