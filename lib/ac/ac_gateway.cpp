#include "ac_gateway.h"
#include <string.h>
#include <vector>
#include <new>

namespace ac {

// ── GwBuffer ──────────────────────────────────────────────────────────────
bool GwBuffer::begin() {
  if (buf_) return true;
  buf_ = new (std::nothrow) uint8_t[(size_t)cap_ * kRecBytes];
  return buf_ != nullptr;
}

bool GwBuffer::push(const uint8_t rec[kRecBytes]) {
  if (!buf_ && !begin()) return false;
  if (count_ == cap_) {
    // Full. The newest reading is more useful than the oldest, so the oldest
    // goes — and it is counted, because a loss nobody counted is the failure
    // this whole project exists to prevent.
    head_ = (head_ + 1) % cap_;
    count_--;
    dropped_++;
  }
  memcpy(buf_ + (size_t)tail_ * kRecBytes, rec, kRecBytes);
  tail_ = (tail_ + 1) % cap_;
  count_++;
  return true;
}

bool GwBuffer::peek(uint8_t rec[kRecBytes]) const {
  if (!count_) return false;
  memcpy(rec, buf_ + (size_t)head_ * kRecBytes, kRecBytes);
  return true;
}

bool GwBuffer::peekAt(uint32_t n, uint8_t rec[kRecBytes]) const {
  if (n >= count_) return false;
  uint32_t slot = (head_ + n) % cap_;
  memcpy(rec, buf_ + (size_t)slot * kRecBytes, kRecBytes);
  return true;
}

void GwBuffer::pop() {
  if (!count_) return;
  head_ = (head_ + 1) % cap_;
  count_--;
}

// ── Gateway ───────────────────────────────────────────────────────────────
bool Gateway::begin() {
  if (!buf_.begin()) return false;
  return radio_.begin();
}

bool Gateway::seenBefore(uint32_t device, uint32_t seq) {
  for (int i = 0; i < nodes_; ++i) {
    if (seenDev_[i] == device) {
      if (seq <= seenSeq_[i]) return true;     // heard it already
      seenSeq_[i] = seq;
      return false;
    }
  }
  if (nodes_ < kMaxNodes) {
    seenDev_[nodes_] = device;
    seenSeq_[nodes_] = seq;
    nodes_++;
    s_.nodes = (uint32_t)nodes_;
  }
  return false;
}

void Gateway::poll() {
  uint8_t rec[kRecBytes];
  int16_t rssi = 0;

  while (radio_.receive(rec, rssi)) {
    s_.received++;
    s_.lastRssi = rssi;

    // Read the two fields the gateway is allowed to care about: who sent it and
    // which reading it is. Everything else is opaque bytes and stays that way.
    const uint8_t* p = rec;
    uint32_t device = (uint32_t)p[0] | (uint32_t)p[1] << 8 |
                      (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
    uint32_t seq    = (uint32_t)p[4] | (uint32_t)p[5] << 8 |
                      (uint32_t)p[6] << 16 | (uint32_t)p[7] << 24;

    // A node that did not hear our acknowledgement will send again. Taking the
    // duplicate is harmless; forwarding it wastes uplink, and the server would
    // refuse it anyway.
    if (seenBefore(device, seq)) {
      s_.duplicates++;
      radio_.ack(device, seq);        // re-acknowledge so it stops asking
      continue;
    }

    uint32_t before = buf_.dropped();
    buf_.push(rec);
    s_.dropped += (buf_.dropped() - before);

    // Hop-by-hop only: this says "I have it", not "the server has it".
    radio_.ack(device, seq);
  }
}

void Gateway::forward() {
  if (buf_.empty() || !up_.up()) return;

  std::vector<uint8_t> out;
  while (!buf_.empty() && up_.up()) {
    uint32_t n = buf_.count() < batch_ ? buf_.count() : batch_;
    out.clear();
    out.reserve((size_t)n * kRecBytes);

    uint8_t rec[kRecBytes];
    uint32_t packed = 0;
    // Peek, do not pop: nothing leaves the buffer until the uplink has taken it.
    // A link that dies mid-batch must cost us nothing.
    for (uint32_t i = 0; i < n; ++i) {
      if (!buf_.peekAt(i, rec)) break;
      out.insert(out.end(), rec, rec + kRecBytes);
      packed++;
    }
    if (!packed) return;

    uint32_t acked = 0;
    if (!up_.send(out.data(), packed, acked)) return;   // keep everything, retry later
    s_.batches++;
    s_.forwarded += packed;
    for (uint32_t i = 0; i < packed; ++i) buf_.pop();
  }
}

}  // namespace ac
