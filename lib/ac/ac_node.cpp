#include "ac_node.h"
#include "ac_sha256.h"
#include <string.h>
#include <vector>

namespace ac {

bool Node::begin() {
  if (!st_.begin())  return false;
  if (!sig_.begin()) return false;
  sns_.begin();                       // a dead sensor is a flagged record, not a stop

  // Pick up where the last power cycle left off, so a reset does not restart
  // the sequence and does not break the chain.
  uint32_t last = st_.lastSeq();
  if (last) {
    uint8_t raw[kRecBytes];
    if (st_.read(last, raw)) {
      Record r; decode(raw, r);
      digest(r, prev_);
      nextSeq_ = last + 1;
      first_ = false;
    }
  }
  acked_ = st_.loadAck();
  if (acked_ > st_.lastSeq()) acked_ = st_.lastSeq();
  return true;
}

void Node::tick() {
  // ── 1. sample ───────────────────────────────────────────────────────────
  Reading v = sns_.read();
  s_.sampled++;
  if (!v.ok) s_.sensorBad++;

  // ── 2. build and chain ──────────────────────────────────────────────────
  Record r;
  r.device = dev_;
  r.seq    = nextSeq_;
  r.ts     = clk_.now();
  r.temp   = v.temp;
  r.rh     = v.rh;
  r.c2h4   = v.c2h4;
  r.batt   = v.batt;
  r.flags  = (uint8_t)((v.tamper   ? FLAG_TAMPER    : 0) |
                       (v.moved    ? FLAG_MOVED     : 0) |
                       (v.charging ? FLAG_CHARGING  : 0) |
                       (v.temp < 0 ? FLAG_COLD      : 0) |
                       (first_     ? FLAG_SELFTEST  : 0) |
                       (!v.ok      ? FLAG_SENSORBAD : 0));
  // A reading the sensor refused to give is still recorded, but it is marked.
  // Without this the server cannot tell a dead SHT40 from a genuine 0.00 C.
  memcpy(r.prev, prev_, 32);

  // ── 3. sign, inside the device ──────────────────────────────────────────
  uint8_t d[32];
  digest(r, d);
  sig_.sign(d, r.sig);

  // ── 4. store, before any radio is touched ───────────────────────────────
  uint8_t raw[kRecBytes];
  encode(r, raw);
  uint32_t oldestBefore = st_.firstSeq();
  if (!st_.append(raw)) return;             // flash refused: nothing else is safe to do
  s_.stored++;
  memcpy(prev_, d, 32);
  nextSeq_++;
  first_ = false;

  // If the ring wrapped past something the server never got, say so out loud
  // rather than quietly losing it.
  uint32_t oldestAfter = st_.firstSeq();
  if (oldestBefore && oldestAfter > oldestBefore && oldestBefore > acked_)
    s_.dropped += (oldestAfter - oldestBefore);

  // ── 5. only now, the radio ──────────────────────────────────────────────
  resync();
}

void Node::resync() {
  if (!link_.up()) return;

  // The server is the authority on what it already holds. Asking is cheaper
  // than guessing, and it is what makes a mid-outage reboot harmless.
  uint32_t serverAck = 0;
  if (link_.queryLastAck(dev_, serverAck)) {
    acked_ = serverAck;
    st_.saveAck(acked_);
  }

  uint32_t last = st_.lastSeq();
  if (acked_ >= last) return;

  // Never try to send what the ring has already overwritten. If the outage
  // outlasted the flash, say exactly which records are gone before sending the
  // ones that survived — otherwise the server's sequence check would refuse
  // everything after the hole for ever, and the hole would go unrecorded.
  uint32_t from = acked_ + 1;
  if (st_.firstSeq() && from < st_.firstSeq()) {
    uint32_t lostTo = st_.firstSeq() - 1;
    uint8_t gd[32], mac[32];
    gapDigest(dev_, from, lostTo, gd);
    if (!sig_.sign(gd, mac)) return;            // no signature, no notice
    if (!link_.declareGap(dev_, from, lostTo, mac)) return;
    s_.gapsDeclared++;
    acked_ = lostTo;
    st_.saveAck(acked_);
    from = st_.firstSeq();
  }

  std::vector<uint8_t> buf;
  while (from <= last) {
    uint32_t n = last - from + 1;
    if (n > batch_) n = batch_;

    buf.clear();
    buf.reserve(n * kRecBytes);
    uint32_t packed = 0;
    for (uint32_t i = 0; i < n; ++i) {
      uint8_t raw[kRecBytes];
      if (!st_.read(from + i, raw)) break;
      buf.insert(buf.end(), raw, raw + kRecBytes);
      packed++;
    }
    if (!packed) break;

    uint32_t acked = 0;
    if (!link_.send(buf.data(), packed, acked)) return;  // link died mid-catch-up:
                                                         // stop, keep everything, retry later
    s_.batches++;
    if (acked <= acked_) return;        // no forward progress, do not spin
    s_.sent += (acked - acked_);
    acked_ = acked;
    st_.saveAck(acked_);
    from = acked_ + 1;
  }
}

uint32_t verifyChain(IStore& st, ISigner& signer) {
  uint32_t first = st.firstSeq(), last = st.lastSeq();
  if (!first) return 0;

  bool havePrev = false;
  uint8_t expect[32] = {0};

  for (uint32_t s = first; s <= last; ++s) {
    uint8_t raw[kRecBytes];
    if (!st.read(s, raw)) return s;

    Record r; decode(raw, r);
    if (r.seq != s) return s;

    uint8_t d[32];
    digest(r, d);
    if (!signer.verify(d, r.sig)) return s;        // edited after signing
    if (havePrev && memcmp(r.prev, expect, 32))    // not the record it claims to follow
      return s;

    memcpy(expect, d, 32);
    havePrev = true;
  }
  return 0;
}

}  // namespace ac
