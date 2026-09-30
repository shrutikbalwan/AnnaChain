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

static inline uint32_t le32(const uint8_t* p) {
  return (uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

bool GwBuffer::peekHead(uint32_t n, uint8_t& kind, uint32_t& device, uint32_t& a,
                        uint32_t& b) const {
  if (n >= count_) return false;
  const uint8_t* slot = buf_ + (size_t)((head_ + n) % cap_) * kSlot;
  kind = slot[0];
  device = le32(slot + 1);
  a = le32(slot + 5);                    // a record's seq, or a gap notice's from
  b = kind == FRAME_GAP ? le32(slot + 9) : a;
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

Gateway::NodeSlot* Gateway::slot(uint32_t device, bool add) {
  for (int i = 0; i < nodes_; ++i)
    if (nodeTab_[i].dev == device) return &nodeTab_[i];
  if (!add || nodes_ >= kMaxNodes) return nullptr;
  nodeTab_[nodes_] = NodeSlot{device, false, 0};
  s_.nodes = (uint32_t)++nodes_;
  return &nodeTab_[nodes_ - 1];
}

void Gateway::remember(uint32_t device, uint32_t serverAck) {
  if (NodeSlot* s = slot(device, true)) { s->known = true; s->ack = serverAck; }
}

void Gateway::refresh(uint32_t device) {
  uint32_t a = 0;
  if (up_.up() && up_.queryLastAck(device, a)) remember(device, a);
}

bool Gateway::isDuplicate(uint32_t device, uint8_t kind, uint32_t a, uint32_t b) const {
  // Already at the server, by the server's own last word on it.
  for (int i = 0; i < nodes_; ++i)
    if (nodeTab_[i].dev == device && nodeTab_[i].known && b <= nodeTab_[i].ack) return true;
  // Or in the buffer now, waiting to go.
  uint8_t k; uint32_t d, x, y;
  for (uint32_t i = 0; buf_.peekHead(i, k, d, x, y); ++i)
    if (d == device && k == kind && x == a && y == b) return true;
  return false;
}

uint32_t Gateway::heldThrough(uint32_t device, uint32_t serverAck) const {
  // Walk this node's frames in queue order: how far past the server's last-ACK
  // does an unbroken run of them reach? A hole (a record lost to overrun, or
  // one that never arrived) stops it there.
  uint32_t next = serverAck + 1;
  uint8_t k; uint32_t d, a, b;
  for (uint32_t i = 0; buf_.peekHead(i, k, d, a, b); ++i) {
    if (d != device) continue;
    if (a < next) continue;                        // already covered
    if (a > next) break;                           // a hole
    next = b + 1;                                  // a record, or a declared gap
  }
  return next - 1;
}

bool Gateway::answerLastAck(uint32_t device, uint32_t& value, uint32_t* unixNow) {
  if (unixNow) *unixNow = 0;
  if (!up_.up()) return false;                     // no value: see ac_gateway.h
  uint32_t a = 0;
  if (!up_.queryLastAck(device, a)) return false;  // the server did not say
  remember(device, a);
  value = heldThrough(device, a);
  if (unixNow) {
    uint32_t t = 0;
    if (up_.serverTime(t))  *unixNow = t;          // the server's, with its answer
    else if (clk_.isSet())  *unixNow = clk_.now(); // or NTP's, if it has answered
  }
  return true;
}

void Gateway::poll() {
  uint8_t rec[kRecBytes];
  uint8_t kind = FRAME_RECORD;
  int16_t rssi = 0;

  while (radio_.receive(rec, kind, rssi)) {
    s_.lastRssi = rssi;

    if (kind == FRAME_QUERY) {
      // A node asking what the server holds for it. Answered from the server,
      // never from what this gateway has acknowledged (ac_gateway.h).
      uint32_t device = le32(rec), v = 0, t = 0;
      bool known = answerLastAck(device, v, &t);
      radio_.lastAck(device, known, v, t);
      continue;
    }

    if (kind == FRAME_GAP) {
      // A node declaring records lost. Queue it exactly as it came: the node's
      // signature covers device, range and all, and only the server checks it.
      // A repeat of it (the node did not hear our ack) is a duplicate like any
      // other.
      uint32_t device = le32(rec), from = le32(rec + 4), to = le32(rec + 8);
      s_.gapsReceived++;
      slot(device, true);
      if (isDuplicate(device, FRAME_GAP, from, to)) {
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
    slot(device, true);

    // A node that did not hear our acknowledgement will send again. Taking the
    // duplicate is harmless; forwarding it wastes uplink, and the server would
    // refuse it anyway.
    if (isDuplicate(device, FRAME_RECORD, seq, seq)) {
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
      refresh(device);
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

    // What did the server actually keep? Ask, per node in the batch: a record
    // it refused is gone from here, and the node must hear that from the
    // server, not from our hop-by-hop ack.
    uint32_t devs[kMaxNodes]; int nd = 0;
    for (uint32_t i = 0; i < packed; ++i) {
      uint32_t d = le32(out.data() + (size_t)i * kRecBytes);
      bool have = false;
      for (int j = 0; j < nd; ++j) have = have || devs[j] == d;
      if (!have && nd < kMaxNodes) devs[nd++] = d;
    }
    for (int j = 0; j < nd; ++j) refresh(devs[j]);
  }
}

}  // namespace ac
