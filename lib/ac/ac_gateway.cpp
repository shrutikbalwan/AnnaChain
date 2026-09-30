#include "ac_gateway.h"
#include <string.h>
#include <vector>
#include <new>

namespace ac {

// ── GwBuffer ──────────────────────────────────────────────────────────────
bool GwBuffer::begin() {
  if (buf_) return true;
  buf_ = new (std::nothrow) uint8_t[(size_t)cap_ * kSlot];
  return buf_ != nullptr;
}

bool GwBuffer::push(const uint8_t frame[kRecBytes], uint8_t kind) {
  if (!buf_ && !begin()) return false;
  if (count_ == cap_) {
    // Full. The newest reading is more useful than the oldest, so the oldest
    // goes — and it is counted, because a loss nobody counted is the failure
    // this whole project exists to prevent. A gap notice is counted too.
    if (buf_[(size_t)head_ * kSlot] == FRAME_GAP) droppedGaps_++;
    else                                          dropped_++;
    head_ = (head_ + 1) % cap_;
    count_--;
  }
  uint8_t* slot = buf_ + (size_t)tail_ * kSlot;
  slot[0] = kind;
  memcpy(slot + 1, frame, kRecBytes);
  tail_ = (tail_ + 1) % cap_;
  count_++;
  return true;
}

bool GwBuffer::peek(uint8_t frame[kRecBytes]) const {
  return peekAt(0, frame);
}

bool GwBuffer::peekAt(uint32_t n, uint8_t frame[kRecBytes]) const {
  uint8_t kind;
  return peekAt(n, frame, kind);
}

bool GwBuffer::peekAt(uint32_t n, uint8_t frame[kRecBytes], uint8_t& kind) const {
  if (n >= count_) return false;
  const uint8_t* slot = buf_ + (size_t)((head_ + n) % cap_) * kSlot;
  kind = slot[0];
  memcpy(frame, slot + 1, kRecBytes);
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

static inline uint32_t le32(const uint8_t* p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

void Gateway::poll() {
  uint8_t rec[kRecBytes];
  uint8_t kind = FRAME_RECORD;
  int16_t rssi = 0;

  while (radio_.receive(rec, kind, rssi)) {
    s_.lastRssi = rssi;

    if (kind == FRAME_GAP) {
      // A node declaring records lost. Queue it exactly as it came: the node's
      // signature covers device, range and all, and only the server checks it.
      // It takes the place of the records it names, so a repeat of it (the
      // node did not hear our ack) is a duplicate like any other.
      uint32_t device = le32(rec), to = le32(rec + 8);
      s_.gapsReceived++;
      if (seenBefore(device, to)) {
        s_.duplicates++;
        radio_.ack(device, to);
        continue;
      }
      buf_.push(rec, FRAME_GAP);
      s_.dropped     = buf_.dropped();
      s_.gapsDropped = buf_.droppedGaps();
      radio_.ack(device, to);
      continue;
    }
    if (kind != FRAME_RECORD) continue;       // not ours to guess at
    s_.received++;

    // Read the two fields the gateway is allowed to care about: who sent it and
    // which reading it is. Everything else is opaque bytes and stays that way.
    uint32_t device = le32(rec);
    uint32_t seq    = le32(rec + 4);

    // A node that did not hear our acknowledgement will send again. Taking the
    // duplicate is harmless; forwarding it wastes uplink, and the server would
    // refuse it anyway.
    if (seenBefore(device, seq)) {
      s_.duplicates++;
      radio_.ack(device, seq);        // re-acknowledge so it stops asking
      continue;
    }

    buf_.push(rec, FRAME_RECORD);
    s_.dropped     = buf_.dropped();
    s_.gapsDropped = buf_.droppedGaps();

    // Hop-by-hop only: this says "I have it", not "the server has it".
    radio_.ack(device, seq);
  }
}

void Gateway::forward() {
  if (buf_.empty() || !up_.up()) return;

  std::vector<uint8_t> out;
  while (!buf_.empty() && up_.up()) {
    uint8_t frame[kRecBytes];
    uint8_t kind = FRAME_RECORD;
    buf_.peekAt(0, frame, kind);

    if (kind == FRAME_GAP) {
      // Handed up as the node sent it. If the uplink does not take it, it stays
      // at the front: nothing behind it may overtake it, because the server
      // must hear about the hole before it hears the records after it.
      uint32_t device, from, to; uint8_t mac[32];
      decodeGapFrame(frame, device, from, to, mac);
      if (!up_.declareGap(device, from, to, mac)) return;
      s_.gapsForwarded++;
      buf_.pop();
      continue;
    }

    uint32_t n = buf_.count() < batch_ ? buf_.count() : batch_;
    out.clear();
    out.reserve((size_t)n * kRecBytes);

    uint8_t rec[kRecBytes];
    uint32_t packed = 0;
    // Peek, do not pop: nothing leaves the buffer until the uplink has taken it.
    // A link that dies mid-batch must cost us nothing. A batch stops at a gap
    // notice, so the notice goes up in its place in the queue.
    for (uint32_t i = 0; i < n; ++i) {
      if (!buf_.peekAt(i, rec, kind) || kind != FRAME_RECORD) break;
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
