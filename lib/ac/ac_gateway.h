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
#pragma once
#include "ac_hal.h"

namespace ac {

// A FIFO of whole records, oldest first. Records from every node share it, in
// arrival order, because that is the order they can be forwarded in.
class GwBuffer {
 public:
  explicit GwBuffer(uint32_t capacity) : cap_(capacity) {}
  ~GwBuffer() { delete[] buf_; }

  bool begin();
  // Returns false only if the record could not be stored at all.
  bool push(const uint8_t rec[kRecBytes]);
  bool peek(uint8_t rec[kRecBytes]) const;
  // Look at the n-th record from the front without removing anything. The
  // forwarder builds a whole batch this way, so a link that dies mid-send
  // costs nothing.
  bool peekAt(uint32_t n, uint8_t rec[kRecBytes]) const;
  void pop();

  uint32_t count()    const { return count_; }
  uint32_t capacity() const { return cap_; }
  uint32_t dropped()  const { return dropped_; }   // overwritten before sending
  bool     empty()    const { return count_ == 0; }

 private:
  uint32_t cap_, head_ = 0, tail_ = 0, count_ = 0, dropped_ = 0;
  uint8_t* buf_ = nullptr;
};

// The LoRa side. One packet in, one record out.
struct IGatewayRadio {
  virtual ~IGatewayRadio() {}
  virtual bool begin() = 0;
  // Non-blocking. Returns false when nothing has arrived.
  virtual bool receive(uint8_t rec[kRecBytes], int16_t& rssi) = 0;
  // Tell a node its record was taken into the gateway's buffer. This is a
  // hop-by-hop acknowledgement, not proof of delivery to the server — the node
  // still reconciles against the server's own last-ACK.
  virtual bool ack(uint32_t device, uint32_t seq) = 0;
};

struct GwStats {
  uint32_t received = 0;
  uint32_t duplicates = 0;     // the same record heard twice
  uint32_t forwarded = 0;
  uint32_t dropped = 0;        // buffer overran before the uplink came back
  uint32_t batches = 0;
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

  // Push buffered records upstream, oldest first. Does nothing when the uplink
  // is down, which is the entire point of the buffer.
  void forward();

  const GwStats& stats() const { return s_; }
  uint32_t buffered() const { return buf_.count(); }

  void setBatchSize(uint32_t n) { batch_ = n ? n : 1; }

 private:
  bool seenBefore(uint32_t device, uint32_t seq);

  static const int kMaxNodes = 16;
  uint32_t id_;
  IClock&         clk_;
  IGatewayRadio&  radio_;
  GwBuffer&       buf_;
  ILink&          up_;
  uint32_t        batch_ = 20;
  uint32_t        seenDev_[kMaxNodes] = {0};
  uint32_t        seenSeq_[kMaxNodes] = {0};
  int             nodes_ = 0;
  GwStats         s_;
};

}  // namespace ac
