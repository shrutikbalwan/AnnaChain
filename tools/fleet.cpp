// AnnaChain — three nodes on one truck, and one of them is lying.
//
//   g++ -std=gnu++17 -DAC_LOG_CAPACITY=4096 -Ilib/ac lib/ac/*.cpp tools/fleet.cpp -o fleet
//   ./fleet [online] [dark] [drift] [--ethylene] [--start <unix>] > fleet.capture
//
// --ethylene simulates ethylene sensors the board does not have. Off by
// default, so the capture carries "not fitted" exactly as the hardware would.
//   py backend/feed_sim.py fleet.capture --reset
//
// This is the slide-2 claim "nodes watch each other" made demonstrable without
// hardware. Three crates in the same reefer, sampling at the same moments. Node
// B's sensor starts drifting an hour in and ends up reading several degrees
// warm — the kind of failure that makes a naive system call spoilage and send a
// driver to check a load that is perfectly fine.
//
// Every node signs with its own key and keeps its own chain. They are compared
// only at the server, by what they report, which is the only place a comparison
// can honestly be made.
#include "ac_node.h"
#include "ac_sim.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <ctime>
#include <vector>

using namespace ac;

static const char* KEYS[3] = {
  "annachain-node-a-dev-key-not-for-field",
  "annachain-node-b-dev-key-not-for-field",
  "annachain-node-c-dev-key-not-for-field",
};
static const uint32_t IDS[3] = {0x26232001u, 0x26232002u, 0x26232003u};
static const char* NAMES[3] = {"Crate node A", "Crate node B", "Crate node C"};

// A link that writes the wire protocol to stdout instead of to a radio.
class DumpLink : public ILink {
 public:
  bool up() override { return up_; }
  bool queryLastAck(uint32_t, uint32_t& a) override { a = ack_; return up_; }
  bool send(const uint8_t* recs, size_t n, uint32_t& acked) override {
    if (!up_) return false;
    for (size_t i = 0; i < n; ++i) {
      std::printf("R ");
      for (size_t b = 0; b < kRecBytes; ++b) std::printf("%02x", recs[i*kRecBytes + b]);
      std::printf("\n");
    }
    Record r; decode(recs + (n - 1) * kRecBytes, r);
    ack_ = r.seq; acked = ack_;
    return true;
  }
  // G <device> <from> <to> <mac-hex>: the notice carries the device's own
  // signature, and the server refuses one without it.
  bool declareGap(uint32_t d, uint32_t from, uint32_t to,
                  const uint8_t mac[32]) override {
    if (!up_) return false;
    char hex[65]; hex32(mac, hex);
    std::printf("G %u %u %u %s\n", d, from, to, hex);
    ack_ = to;
    return true;
  }
  void setUp(bool u) { up_ = u; }
 private:
  bool up_ = true;
  uint32_t ack_ = 0;
};

struct NodeRig {
  SimSensors sns;
  MemStore   store{4096};
  SoftSigner signer;
  DumpLink   link;
  Node*      node = nullptr;

  NodeRig(uint32_t seed, const char* key) : sns(seed), signer(key) {}
};

// Where the simulated trip starts. By default it ends at the moment the capture
// is made, because the server refuses readings older than a node could have
// held them (backend/checks.py, check 5). --start pins it for a reproducible run.
static uint32_t tripStart(long explicitStart, int readings) {
  if (explicitStart > 0) return (uint32_t)explicitStart;
  uint32_t now = (uint32_t)time(nullptr);
  uint32_t start = now - (uint32_t)readings * 300u;
  return start - start % 300u;          // align to the 5-minute sampling grid
}

int main(int argc, char** argv) {
  int online = 300, dark = 120;
  double drift = 6.5;                 // how far node B ends up off, in degrees
  bool ethylene = false;
  long start = 0;
  int pos = 0;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "--ethylene")) ethylene = true;
    else if (!strcmp(argv[i], "--start") && i + 1 < argc) start = atol(argv[++i]);
    else if (pos == 0) { online = atoi(argv[i]); pos++; }
    else if (pos == 1) { dark   = atoi(argv[i]); pos++; }
    else if (pos == 2) { drift  = atof(argv[i]); pos++; }
  }

  SimClock clk(tripStart(start, online + dark));

  std::vector<NodeRig*> rigs;
  for (int i = 0; i < 3; ++i) {
    NodeRig* r = new NodeRig(11 + i * 7, KEYS[i]);
    r->sns.setEthyleneFitted(ethylene);
    r->node = new Node(IDS[i], clk, r->sns, r->store, r->link, r->signer);
    r->node->setBatchSize(20);
    r->node->begin();
    rigs.push_back(r);

    std::printf("K %u ", IDS[i]);
    for (const char* p = KEYS[i]; *p; ++p) std::printf("%02x", (unsigned char)*p);
    std::printf("\nL %u TRUCK-MH15-4417 %s\n", IDS[i], NAMES[i]);
  }

  const int total = online + dark;
  const int driftStart = online / 3;   // the sensor is fine, then it is not

  for (int i = 0; i < total; ++i) {
    if (i == online) {                 // into the blind spot, all three at once
      std::printf("# offline\n");
      for (auto* r : rigs) r->link.setUp(false);
    }

    // Node B's calibration walks away, slowly enough that no single reading
    // looks wrong. That is what makes it hard, and what makes the majority
    // vote worth having.
    if (i >= driftStart) {
      double f = double(i - driftStart) / double(total - driftStart);
      rigs[1]->sns.setBias(drift * f);
    }

    // One shared event: a door opened at a checkpoint. All three should see it,
    // which is exactly how you tell a real excursion from a broken sensor.
    if (i == online * 55 / 100) for (auto* r : rigs) r->sns.openDoor(35);

    for (auto* r : rigs) {
      r->sns.setBattery((uint8_t)(100 - (i * 20) / total));
      r->node->tick();
    }
    clk.advance(300);
  }

  std::printf("# online\n");
  for (auto* r : rigs) { r->link.setUp(true); r->node->resync(); }
  std::printf("# done\n");

  std::fprintf(stderr,
    "three nodes, %d readings each (%d online, %d through the outage)\n"
    "node B drifts to +%.1f C by the end; A and C stay honest\n"
    "ethylene: %s\n",
    total, online, dark, drift,
    ethylene ? "SIMULATED (--ethylene), flagged in every record (FLAG_SIMULATED). "
               "The board has no ethylene sensor."
             : "not fitted, as on the real board");
  return 0;
}
