// AnnaChain — the node.
//
// The whole claim of this project lives in tick() and resync(), and it is
// three sentences long:
//
//   1. Build the record, chain it to the one before, sign it inside the
//      device, and write it to flash. Only then think about the radio.
//   2. If there is no link, do nothing else. Keep sampling. Keep storing.
//   3. When the link returns, ask the server what it last had, and send only
//      what is missing — in order.
//
// A connectivity blind spot therefore cannot create a gap in the record. It
// can only delay it.
#pragma once
#include "ac_hal.h"

namespace ac {

struct Stats {
  uint32_t sampled   = 0;   // readings taken
  uint32_t stored    = 0;   // records written to flash
  uint32_t sent      = 0;   // records the server accepted
  uint32_t dropped   = 0;   // lost to ring wrap before they could be sent
  uint32_t sensorBad = 0;   // readings the sensor refused to give
  uint32_t batches   = 0;
  uint32_t gapsDeclared = 0;  // holes reported to the server, never hidden
};

class Node {
 public:
  Node(uint32_t deviceId, IClock& clk, ISensors& sns, IStore& st,
       ILink& link, ISigner& signer)
      : dev_(deviceId), clk_(clk), sns_(sns), st_(st), link_(link), sig_(signer) {}

  bool begin();

  // One cycle: sample, chain, sign, store, then try to catch up.
  void tick();

  // Send everything the server has not confirmed, oldest first.
  // Safe to call at any time; does nothing when the link is down.
  void resync();

  const Stats&   stats() const { return s_; }
  uint32_t       ackedSeq() const { return acked_; }
  uint32_t       pending()  const {
    uint32_t last = st_.lastSeq();
    return last > acked_ ? last - acked_ : 0;
  }

  // How many records to put in one radio batch.
  void setBatchSize(uint32_t n) { batch_ = n ? n : 1; }

 private:
  uint32_t dev_;
  IClock&   clk_;
  ISensors& sns_;
  IStore&   st_;
  ILink&    link_;
  ISigner&  sig_;

  uint8_t  prev_[32] = {0};   // digest of the last record written
  uint32_t nextSeq_  = 1;
  uint32_t acked_    = 0;
  uint32_t batch_    = 20;
  bool     first_    = true;
  // Time (docs/HIL.md step 1; checks.py check 5).
  //   lastTs_   the timestamp of the newest record in flash. No record is ever
  //             stamped earlier, so a reboot, or a server time a little behind
  //             this clock, cannot make time run backwards.
  //   clockSet_ the node has had wall-clock time since it last powered up, or
  //             the newest record in flash says it had. Records stamped while
  //             it is false carry FLAG_TIMEUNSET; once true it never goes back.
  uint32_t lastTs_   = 0;
  bool     clockSet_ = false;
  Stats    s_;
};

// Walk the chain from firstSeq to lastSeq and check that each record's prev[]
// really is the digest of the record before it, and that the signature holds.
// Returns 0 if the chain is whole, otherwise the sequence number where it broke.
uint32_t verifyChain(IStore& st, ISigner& signer);

}  // namespace ac
