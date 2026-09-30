// AnnaChain — laptop stand-ins for the parts that have not arrived yet.
//
// These are not toys. The sensor model produces the kind of curve a grape
// consignment actually draws on a September run to JNPT, the link can be
// switched off mid-journey, and the server keeps its own copy and its own
// idea of what it has received. That is enough to prove the claim the deck
// makes, on a laptop, tonight.
#pragma once
#include "ac_hal.h"
#include "ac_gateway.h"
#include "ac_sha256.h"
#include <map>
#include <vector>
#include <string.h>

namespace ac {

// ── clock you can fast-forward ────────────────────────────────────────────
class SimClock : public IClock {
 public:
  explicit SimClock(uint32_t start) : t_(start) {}
  uint32_t now() override { return t_; }
  void sleep(uint32_t ms) override { t_ += ms / 1000; }
  void advance(uint32_t sec) { t_ += sec; }
 private:
  uint32_t t_;
};

// ── a plausible cold chain, not a random number generator ────────────────
// Ambient drifts on a daily cycle, the reefer pulls it back down, and a door
// opening at a checkpoint puts a spike in it. Optionally one sensor can be
// told to lie, so cross-node disagreement can be demonstrated.
class SimSensors : public ISensors {
 public:
  explicit SimSensors(uint32_t seed = 1) : rnd_(seed) {}
  bool begin() override { return true; }
  Reading read() override;

  void setReefer(bool on)        { reefer_ = on; }
  void setBias(double degC)      { bias_ = degC; }      // a drifting sensor
  // Off by default, because the board has no ethylene sensor. Turning it on
  // simulates hardware we do not have, so say so wherever it is shown.
  void setEthyleneFitted(bool f) { c2h4Fitted_ = f; }
  void openDoor(int minutes)     { door_ = minutes; }
  void setTamper(bool t)         { tamper_ = t; }
  void setBattery(uint8_t pct)   { batt_ = pct; }
  void setFaulty(bool f)         { faulty_ = f; }       // sensor stops answering

 private:
  double  next();
  uint32_t rnd_;
  double  t_      = 0;          // minutes into the trip
  double  bias_   = 0;
  bool    reefer_ = true;
  bool    c2h4Fitted_ = false;  // like the real board: no ethylene sensor chosen yet
  double  c2h4Acc_    = 0;      // accumulated warmth, per sensor, not shared
  bool    tamper_ = false;
  bool    faulty_ = false;
  int     door_   = 0;
  uint8_t batt_   = 100;
};

// ── the flash log, in RAM ─────────────────────────────────────────────────
class MemStore : public IStore {
 public:
  explicit MemStore(uint32_t cap) : cap_(cap) {}
  bool begin() override { return true; }
  bool append(const uint8_t rec[kRecBytes]) override;
  bool read(uint32_t seq, uint8_t rec[kRecBytes]) override;
  uint32_t firstSeq() const override { return first_; }
  uint32_t lastSeq()  const override { return last_; }
  uint32_t count()    const override { return (uint32_t)ring_.size(); }
  uint32_t capacity() const override { return cap_; }
  uint32_t loadAck() override { return ack_; }
  bool saveAck(uint32_t seq) override { ack_ = seq; return true; }

  // For the tamper demo: reach in and change a stored reading, the way an
  // attacker with database access would.
  bool forceEdit(uint32_t seq, int16_t newTemp);

 private:
  uint32_t cap_, first_ = 0, last_ = 0, ack_ = 0;
  std::map<uint32_t, std::vector<uint8_t>> ring_;
};

// ── the server, and the radio between ─────────────────────────────────────
class SimServer {
 public:
  // The eight checks the backend runs before anything is stored.
  struct Result { uint32_t accepted = 0; uint32_t rejected = 0; };

  bool accept(const uint8_t* recs, size_t count, ISigner& signer);
  // Records a hole the device reported, and re-anchors the chain after it.
  void noteGap(uint32_t device, uint32_t from, uint32_t to, const uint8_t mac[32]);
  uint32_t lastAck(uint32_t device) const {
    auto it = ack_.find(device);
    return it == ack_.end() ? 0 : it->second;
  }
  size_t held() const { return db_.size(); }
  struct Gap { uint32_t device, from, to; uint8_t mac[32]; };
  const std::vector<Gap>& gaps() const { return gaps_; }
  const std::vector<Record>& records() const { return db_; }
  uint32_t rejected() const { return rejected_; }
  const char* lastReject() const { return why_; }

 private:
  std::map<uint32_t, uint32_t> ack_;
  std::map<uint32_t, std::vector<uint8_t>> tip_;   // digest of the last accepted record
  std::vector<Record> db_;
  std::vector<Gap> gaps_;
  std::map<uint32_t, bool> anchorNext_;   // accept the next record's prev as the new anchor
  uint32_t rejected_ = 0;
  const char* why_ = "";
};

class SimLink : public ILink {
 public:
  SimLink(SimServer& srv, ISigner& signer) : srv_(srv), signer_(signer) {}
  bool up() override { return up_; }
  bool queryLastAck(uint32_t device, uint32_t& lastAck) override;
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override;
  bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override;

  void setUp(bool u) { up_ = u; }
  void dropNextSend() { dropOnce_ = true; }    // the link dies mid-batch
  uint32_t bytesSent() const { return bytes_; }

 private:
  SimServer& srv_;
  ISigner&   signer_;
  bool up_ = true, dropOnce_ = false;
  uint32_t bytes_ = 0;
};

// ── a LoRa link between the crate nodes and the gateway ───────────────────
// Records handed in by nodes queue here until the gateway polls for them. A
// packet loss rate can be set, because LoRa inside a steel trailer is not a
// perfect pipe and a gateway that only works on a perfect pipe is not a design.
class SimRadio : public IGatewayRadio {
 public:
  bool begin() override { return true; }
  bool receive(uint8_t frame[kRecBytes], uint8_t& kind, int16_t& rssi) override;
  bool ack(uint32_t device, uint32_t seq) override {
    acks_.push_back({device, seq});
    return true;
  }
  bool lastAck(uint32_t device, bool known, uint32_t seq) override {
    replies_.push_back({device, known, seq});
    return true;
  }
  struct Reply { uint32_t device; bool known; uint32_t seq; };
  const std::vector<Reply>& replies() const { return replies_; }

  // called by the node side
  void transmit(const uint8_t frame[kRecBytes], uint8_t kind = FRAME_RECORD);
  void setLossPercent(int pct) { loss_ = pct; }
  void setRssi(int16_t r) { rssi_ = r; }
  size_t queued() const { return q_.size(); }
  uint32_t lost() const { return lost_; }

  struct Ack { uint32_t device, seq; };
  const std::vector<Ack>& acks() const { return acks_; }
  // Every record frame a node put on the air, (device, seq), in order — lost
  // or not. For tests that need to know what a node chose to (re)send.
  const std::vector<Ack>& transmitted() const { return sent_; }

 private:
  std::vector<std::vector<uint8_t>> q_;     // kind byte, then the frame
  std::vector<Ack> acks_;
  std::vector<Ack> sent_;
  std::vector<Reply> replies_;
  int loss_ = 0;
  uint32_t lost_ = 0, rnd_ = 99;
  int16_t rssi_ = -92;
};

// An ILink that hands a node's records to a SimRadio instead of to a server,
// so a node can be wired to a gateway exactly as it would be on a truck.
//
// attach(gateway) makes it behave like LoraNodeLink: queryLastAck() puts a
// FRAME_QUERY on the air and returns the gateway's answer, which is the
// server's last-ACK (ac_gateway.h), or false for "no value". Unattached, it
// answers with its own hop-by-hop ack, which is how nodes behaved before the
// gateway relayed the server's answer; the older gateway tests use it that way.
// gatewayRuns = true also lets the gateway poll and forward after every batch
// the node sends, as the real gateway's loop does while a node is transmitting
// one frame at a time.
class SimNodeToGateway : public ILink {
 public:
  explicit SimNodeToGateway(SimRadio& r) : radio_(r) {}
  bool up() override { return up_; }
  bool queryLastAck(uint32_t device, uint32_t& lastAck) override;
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override;
  // The signed notice goes over LoRa like a record, and the gateway carries
  // it upstream in its place in the queue.
  bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override;
  void setUp(bool u) { up_ = u; }
  void attach(Gateway* gw, bool gatewayRuns = false) { gw_ = gw; runs_ = gatewayRuns; }
 private:
  SimRadio& radio_;
  Gateway* gw_ = nullptr;
  bool runs_ = false;
  bool up_ = true;
  uint32_t ack_ = 0;
};

// ── the secure element, in software ───────────────────────────────────────
// Same interface the ATECC608B driver will present. The key is compiled in
// here only because there is no secure element yet to hold it.
class SoftSigner : public ISigner {
 public:
  explicit SoftSigner(const char* key = "annachain-dev-key-not-for-field")
      : key_(key) {}
  bool begin() override { return true; }
  bool sign(const uint8_t d[32], uint8_t sig[32]) override {
    hmac_sha256((const uint8_t*)key_, strlen(key_), d, 32, sig);
    return true;
  }
  bool verify(const uint8_t d[32], const uint8_t sig[32]) override {
    uint8_t want[32];
    hmac_sha256((const uint8_t*)key_, strlen(key_), d, 32, want);
    return memcmp(want, sig, 32) == 0;
  }
 private:
  const char* key_;
};

}  // namespace ac
