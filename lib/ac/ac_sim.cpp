#include "ac_sim.h"
#include <math.h>

#ifndef M_PI                       // MSVC, and gcc in strict-ANSI mode, do not define it
#define M_PI 3.14159265358979323846
#endif

namespace ac {

// ── SimSensors ────────────────────────────────────────────────────────────
double SimSensors::next() {
  rnd_ = rnd_ * 1103515245u + 12345u;
  return ((rnd_ >> 16) & 0x7fff) / 32767.0;          // 0..1
}

Reading SimSensors::read() {
  Reading v;
  t_ += 5;                                            // one sample every 5 minutes

  if (faulty_) { v.ok = false; v.batt = batt_; return v; }

  // Ambient on a daily cycle, roughly a September run through Maharashtra.
  double ambient = 28.0 + 6.0 * sin((t_ / 1440.0) * 2 * M_PI - 1.2);
  double target  = reefer_ ? 4.0 : ambient;           // grapes ride at 2-5 C
  double noise   = (next() - 0.5) * 0.30;
  double temp    = target + noise + bias_;

  if (door_ > 0) { temp += 6.0 * (door_ / 20.0); door_ -= 5; }   // door open at a checkpoint

  double rh = reefer_ ? 90.0 + (next() - 0.5) * 4.0 : 55.0 + (next() - 0.5) * 10.0;

  v.temp = (int16_t)lround(temp * 100.0);
  v.rh   = (uint16_t)lround(rh * 100.0);

  // Ethylene climbs slowly with accumulated time above 8 C — the physical
  // reason a warm stretch matters, not a random walk.
  if (c2h4Fitted_) {
    if (temp > 8.0) c2h4Acc_ += (temp - 8.0) * 0.4;
    double ppb = 20.0 + c2h4Acc_;
    if (ppb > 65535) ppb = 65535;
    v.c2h4 = (uint16_t)ppb;
    v.simulated = true;              // there is no such sensor: say so, in the record
  } else {
    v.c2h4 = kEthyleneNotFitted;
  }

  v.batt     = batt_;
  v.tamper   = tamper_;
  v.moved    = next() > 0.15;                         // a truck, mostly moving
  v.charging = (fmod(t_, 1440.0) > 400 && fmod(t_, 1440.0) < 1080);  // daylight
  v.ok = true;
  return v;
}

// ── MemStore ──────────────────────────────────────────────────────────────
bool MemStore::append(const uint8_t rec[kRecBytes]) {
  const uint8_t* p = rec + 4;                          // seq is the second field
  uint32_t seq = (uint32_t)p[0] | (uint32_t)p[1] << 8 |
                 (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;

  ring_[seq] = std::vector<uint8_t>(rec, rec + kRecBytes);
  last_ = seq;
  if (!first_) first_ = seq;

  while (ring_.size() > cap_) {                        // wrap: the oldest goes
    ring_.erase(ring_.begin());
    first_ = ring_.begin()->first;
  }
  return true;
}

bool MemStore::read(uint32_t seq, uint8_t rec[kRecBytes]) {
  auto it = ring_.find(seq);
  if (it == ring_.end()) return false;
  memcpy(rec, it->second.data(), kRecBytes);
  return true;
}

bool MemStore::forceEdit(uint32_t seq, int16_t newTemp) {
  auto it = ring_.find(seq);
  if (it == ring_.end()) return false;
  it->second[12] = (uint8_t)(newTemp);                 // temp is at offset 12
  it->second[13] = (uint8_t)(newTemp >> 8);
  return true;
}

// ── SimServer: the eight checks ───────────────────────────────────────────
bool SimServer::accept(const uint8_t* recs, size_t count, ISigner& signer) {
  for (size_t i = 0; i < count; ++i) {
    Record r;
    decode(recs + i * kRecBytes, r);

    // 1. is the device registered
    if (r.device == 0) { rejected_++; why_ = "unknown device"; return false; }

    // 2. is the signature valid  (this is the one that catches an edited record)
    uint8_t d[32];
    digest(r, d);
    if (!signer.verify(d, r.sig)) { rejected_++; why_ = "bad signature"; return false; }

    // 3. is the sequence correct — exactly one more than the last accepted
    uint32_t have = lastAck(r.device);
    if (r.seq != have + 1) {
      // 4. duplicate or replay
      if (r.seq <= have) { rejected_++; why_ = "duplicate or replay"; return false; }
      rejected_++; why_ = "sequence gap"; return false;
    }

    // 5. is the timestamp sensible
    if (r.ts < 1600000000u) { rejected_++; why_ = "impossible timestamp"; return false; }

    // 6. is the hash chain unbroken.  The one exception is the first record
    //    after a hole the device itself declared: there is nothing to chain to,
    //    and the hole is already on the record.
    auto t = tip_.find(r.device);
    if (t != tip_.end() && memcmp(r.prev, t->second.data(), 32) != 0) {
      rejected_++; why_ = "broken hash chain"; return false;
    }
    // A device we have never heard from is allowed to start its own chain.
    // The condition is "this device has sent before", not "the database has
    // anything in it" — with several nodes on one truck those are very
    // different questions, and getting it wrong refuses every node but the
    // first one.
    if (t == tip_.end() && !anchorNext_[r.device] && have > 0) {
      rejected_++; why_ = "no chain anchor"; return false;
    }
    anchorNext_[r.device] = false;

    // 7. is the calibration still valid  (EN 13486 — placeholder until a real
    //    calibration registry exists; the shape of the check is the point)
    // 8. is the sensor healthy — a reading outside the sensor's own rated
    //    range is a sensor fault, not a cold-chain event
    if (r.temp < -4000 || r.temp > 12500) {
      rejected_++; why_ = "outside sensor range"; return false;
    }

    db_.push_back(r);
    ack_[r.device] = r.seq;
    tip_[r.device] = std::vector<uint8_t>(d, d + 32);
  }
  return true;
}

void SimServer::noteGap(uint32_t device, uint32_t from, uint32_t to,
                        const uint8_t mac[32]) {
  Gap g{device, from, to, {0}};
  memcpy(g.mac, mac, 32);
  gaps_.push_back(g);
  ack_[device] = to;             // the server will never see these; stop waiting
  tip_.erase(device);            // the chain restarts after the hole
  anchorNext_[device] = true;
}

// ── SimLink ───────────────────────────────────────────────────────────────
bool SimLink::queryLastAck(uint32_t device, uint32_t& lastAck) {
  if (!up_) return false;
  lastAck = srv_.lastAck(device);
  return true;
}

bool SimLink::send(const uint8_t* recs, size_t count, uint32_t& acked) {
  if (!up_) return false;
  if (dropOnce_) { dropOnce_ = false; up_ = false; return false; }  // died mid-batch

  if (!srv_.accept(recs, count, signer_)) {
    acked = srv_.lastAck(0);
    return true;                        // the link worked; the server said no
  }
  bytes_ += (uint32_t)(count * kRecBytes);

  Record r;
  decode(recs + (count - 1) * kRecBytes, r);
  acked = srv_.lastAck(r.device);
  return true;
}

bool SimLink::declareGap(uint32_t device, uint32_t from, uint32_t to,
                         const uint8_t mac[32]) {
  if (!up_) return false;
  // The server's side of it: a notice the device did not sign is refused, and
  // nothing about the record changes.
  uint8_t d[32];
  gapDigest(device, from, to, d);
  if (!signer_.verify(d, mac)) return false;
  srv_.noteGap(device, from, to, mac);
  return true;
}

// ── SimRadio ──────────────────────────────────────────────────────────────
void SimRadio::transmit(const uint8_t frame[kRecBytes], uint8_t kind) {
  if (kind == FRAME_RECORD) {
    const uint8_t* p = frame;
    sent_.push_back({(uint32_t)p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24,
                     (uint32_t)p[4] | (uint32_t)p[5] << 8 | (uint32_t)p[6] << 16 | (uint32_t)p[7] << 24});
  }
  if (loss_ > 0) {
    rnd_ = rnd_ * 1103515245u + 12345u;
    if ((int)((rnd_ >> 16) % 100) < loss_) { lost_++; return; }   // packet lost
  }
  std::vector<uint8_t> pkt(1 + kRecBytes);
  pkt[0] = kind;
  memcpy(pkt.data() + 1, frame, kRecBytes);
  q_.push_back(pkt);
}

bool SimRadio::receive(uint8_t frame[kRecBytes], uint8_t& kind, int16_t& rssi) {
  if (q_.empty()) return false;
  kind = q_.front()[0];
  memcpy(frame, q_.front().data() + 1, kRecBytes);
  q_.erase(q_.begin());
  rssi = rssi_;
  return true;
}

// ── SimNodeToGateway ──────────────────────────────────────────────────────
bool SimNodeToGateway::queryLastAck(uint32_t device, uint32_t& lastAck) {
  if (!up_) return false;
  if (!gw_) { lastAck = ack_; return true; }     // a node that never asks (see ac_sim.h)

  uint8_t q[kRecBytes] = {0};
  q[0] = (uint8_t)device; q[1] = (uint8_t)(device >> 8);
  q[2] = (uint8_t)(device >> 16); q[3] = (uint8_t)(device >> 24);
  size_t before = radio_.replies().size();
  radio_.transmit(q, FRAME_QUERY);
  gw_->poll();                                   // the gateway hears it, in order
  const auto& rs = radio_.replies();
  for (size_t i = before; i < rs.size(); ++i) {
    if (rs[i].device != device) continue;
    if (!rs[i].known) return false;              // no value: keep what we know
    lastAck = rs[i].seq;
    return true;
  }
  return false;                                  // the query or its answer was lost
}

bool SimNodeToGateway::send(const uint8_t* recs, size_t count, uint32_t& acked) {
  if (!up_) return false;
  for (size_t i = 0; i < count; ++i) radio_.transmit(recs + i * kRecBytes);
  if (gw_ && runs_) { gw_->poll(); gw_->forward(); }

  // The gateway acknowledges hop-by-hop, so from the node's point of view a
  // record handed to the radio is delivered. If the packet was actually lost in
  // the air, the server's last-ACK will say so at the next reconciliation and
  // the node will send it again. That is the whole reason the node reconciles
  // against the server and not against the gateway.
  Record r;
  decode(recs + (count - 1) * kRecBytes, r);
  ack_ = r.seq;
  acked = ack_;
  return true;
}

bool SimNodeToGateway::declareGap(uint32_t device, uint32_t from, uint32_t to,
                                  const uint8_t mac[32]) {
  if (!up_) return false;
  uint8_t frame[kRecBytes];
  encodeGapFrame(device, from, to, mac, frame);
  radio_.transmit(frame, FRAME_GAP);
  ack_ = to;                          // hop-by-hop, exactly as for records
  return true;
}

}  // namespace ac
