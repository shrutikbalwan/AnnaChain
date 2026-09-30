// AnnaChain — the truck gateway.
//
// One of these rides in the cab. Every crate node in the trailer sends to it
// over LoRa; it holds what it receives and pushes it upstream whenever there is
// a mobile signal. That is the second level of the two-level buffering on
// slide 2: the node saves, and the gateway saves again.
//
// The single most important property of this file is what it does NOT do.
//
// The gateway has no signing key. It cannot sign a record, so it cannot invent
// one, and it cannot alter one without the server noticing — every record it
// carries was signed inside a node's secure element and is checked again at the
// server. A compromised gateway can therefore delay data, or lose data, or
// replay data. It cannot make data up. That is why one shared gateway per
// truck is an acceptable design and not a hole in the middle of it.
//
// It follows that this file only ever copies bytes. If you find yourself
// wanting to parse a reading and "fix" it here, stop: that is the oracle
// problem walking back in through the side door.
//
// Gap notices get the same treatment. When a node's flash overruns, the node
// signs a notice saying which records are gone. The gateway queues that notice
// in the same FIFO as the records, so it reaches the server after the records
// before the hole and before the ones after it, and hands it upstream exactly
// as the node sent it. It reads the notice's fields only to pass them to the
// uplink; it cannot change one without the server's signature check failing.
//
// ── What a node is told when it asks "what does the server have?" ─────────
// The node reconciles against the server, not against the gateway: that is
// what makes a lost gateway buffer (the cab loses power; the buffer is RAM) or
// a gateway overrun recoverable. So a FRAME_QUERY is answered like this:
//
//   * The uplink is up and the server answers: the server's last-ACK for that
//     node, extended over the records (and gap notices) this gateway holds for
//     it contiguously after that point. Those are on their way; asking the
//     node for them again would only spend airtime. A record the gateway lost
//     breaks the run, so the node is asked to resend from there.
//   * The uplink is down, or the server did not answer: NO VALUE. The node
//     keeps what it last knew and carries on. Deliberately neither of the two
//     easy answers: the gateway's own hop-by-hop ack says "I have it" about
//     records it may since have lost, which is the bug this replaces; zero
//     makes the node resend its whole flash over LoRa. And no answer is
//     computed from a cached server value while the uplink is down: a gateway
//     that has overrun would then ask for records it has nowhere to put, every
//     sample, for the whole outage.
//
// ── Time ───────────────────────────────────────────────────────────────────
// Nodes stamp their own records, so a node needs the time from somewhere, and
// the gateway is the only thing it can hear. The answer to a FRAME_QUERY
// therefore also carries a time: the server's, as the uplink reported it with
// the last-ACK, or else the gateway's own clock if NTP has set it. Only with an
// answer (known = true): a gateway with no uplink says nothing about anything.
// The gateway cannot sign time any more than it can sign records; what it can
// do with it is bounded by the node (it never steps its clock backwards) and by
// the server's check 5 (docs/CRYPTO.md).
//
// A record is a duplicate if it is in the buffer now, or at or below the last
// last-ACK the server gave for its node. Not "at or below the highest sequence
// heard": after an overrun that rule would throw away exactly the records the
// node was asked to resend.
#pragma once
#include "ac_hal.h"

namespace ac {

// A FIFO of whole frames, oldest first: records and gap notices from every
// node share it, in arrival order, because that is the order they can be
// forwarded in.
class GwBuffer {
 public:
  explicit GwBuffer(uint32_t capacity) : cap_(capacity) {}
  ~GwBuffer() { delete[] buf_; }

  bool begin();
  // Returns false only if the frame could not be stored at all.
  bool push(const uint8_t frame[kRecBytes], uint8_t kind = FRAME_RECORD);
  bool peek(uint8_t frame[kRecBytes]) const;
  // Look at the n-th frame from the front without removing anything. The
  // forwarder builds a whole batch this way, so a link that dies mid-send
  // costs nothing.
  bool peekAt(uint32_t n, uint8_t frame[kRecBytes]) const;
  bool peekAt(uint32_t n, uint8_t frame[kRecBytes], uint8_t& kind) const;
  // The first 12 bytes of the n-th frame, decoded: its device and, for a
  // record, a = seq; for a gap notice, a = from and b = to.
  bool peekHead(uint32_t n, uint8_t& kind, uint32_t& device, uint32_t& a,
                uint32_t& b) const;
  void pop();

  uint32_t count()       const { return count_; }
  uint32_t capacity()    const { return cap_; }
  uint32_t dropped()     const { return dropped_; }      // records overwritten before sending
  uint32_t droppedGaps() const { return droppedGaps_; }  // gap notices overwritten, likewise
  bool     empty()       const { return count_ == 0; }

 private:
  static const size_t kSlot = kRecBytes + 1;   // the kind, then the frame
  uint32_t cap_, head_ = 0, tail_ = 0, count_ = 0, dropped_ = 0, droppedGaps_ = 0;
  uint8_t* buf_ = nullptr;
};

// The LoRa side. One packet in, one frame out: a kind byte, then kRecBytes.
struct IGatewayRadio {
  virtual ~IGatewayRadio() {}
  virtual bool begin() = 0;
  // Non-blocking. Returns false when nothing has arrived.
  virtual bool receive(uint8_t frame[kRecBytes], uint8_t& kind, int16_t& rssi) = 0;
  // Tell a node its record was taken into the gateway's buffer. This is a
  // hop-by-hop acknowledgement, not proof of delivery to the server — the node
  // still reconciles against the server's own last-ACK.
  virtual bool ack(uint32_t device, uint32_t seq) = 0;
  // Answer a node's FRAME_QUERY. known = false means "no value" (see above).
  // unixNow is the time to give the node (0 = none): see "time" above.
  virtual bool lastAck(uint32_t device, bool known, uint32_t seq, uint32_t unixNow) = 0;
};

struct GwStats {
  uint32_t received = 0;
  uint32_t duplicates = 0;     // the same record heard twice
  uint32_t forwarded = 0;
  uint32_t dropped = 0;        // records overwritten: buffer overran before the uplink
                               // came back. Not evidence (unsigned, never sent up): the
                               // node resends them when the server's last-ACK says so
  uint32_t batches = 0;
  uint32_t gapsReceived = 0;   // gap notices heard from nodes
  uint32_t gapsForwarded = 0;  // and handed upstream
  uint32_t gapsDropped = 0;    // lost to overrun, counted exactly as records are
  uint32_t nodes = 0;          // distinct devices heard from
  int16_t  lastRssi = 0;
};

class Gateway {
 public:
  Gateway(uint32_t id, IClock& clk, IGatewayRadio& radio, GwBuffer& buf, ILink& up)
      : id_(id), clk_(clk), radio_(radio), buf_(buf), up_(up) {}

  bool begin();

  // Drain whatever the radio has heard into the buffer. Call often.
  void poll();

  // Push buffered frames upstream, oldest first: records in batches, gap
  // notices one at a time, in queue order. Does nothing when the uplink is
  // down, which is the entire point of the buffer. A frame the uplink does not
  // take stays at the front and is tried again next time.
  void forward();

  const GwStats& stats() const { return s_; }
  uint32_t buffered() const { return buf_.count(); }

  void setBatchSize(uint32_t n) { batch_ = n ? n : 1; }

  // What to tell a node that asks what the server holds (see the top of this
  // file). False means no value: the uplink is down or the server did not say.
  // unixNow, if given, is the time to pass down with the answer (0 = none).
  bool answerLastAck(uint32_t device, uint32_t& value, uint32_t* unixNow = nullptr);

 private:
  struct NodeSlot { uint32_t dev; bool known; uint32_t ack; };
  NodeSlot* slot(uint32_t device, bool add);
  void      remember(uint32_t device, uint32_t serverAck);
  void      refresh(uint32_t device);
  bool      isDuplicate(uint32_t device, uint8_t kind, uint32_t a, uint32_t b) const;
  uint32_t  heldThrough(uint32_t device, uint32_t serverAck) const;

  static const int kMaxNodes = 16;
  uint32_t id_;
  IClock&         clk_;
  IGatewayRadio&  radio_;
  GwBuffer&       buf_;
  ILink&          up_;
  uint32_t        batch_ = 20;
  NodeSlot        nodeTab_[kMaxNodes] = {};
  int             nodes_ = 0;
  GwStats         s_;
};

}  // namespace ac
