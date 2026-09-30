// AnnaChain — produce a capture file the Python server can replay.
//
//   g++ -std=gnu++17 -Ilib/ac lib/ac/*.cpp tools/dump.cpp -o dump
//   ./dump [online] [dark] [--gateway] [--ethylene] [--start <unix>] > demo.capture
//   python3 backend/feed_sim.py demo.capture
//
// --gateway puts the truck gateway in the path, as on real hardware: node ->
// LoRa -> gateway -> uplink. The crate drops out of LoRa range for [dark]
// readings, long enough for its 4096-record flash to overrun, and comes back
// while the cab still has no signal. The node's signed gap notice then waits in
// the gateway's buffer with the backlog, and goes up in its place in the queue
// when the uplink returns. What this prints is what the gateway sent upstream.
//
// --ethylene simulates an ethylene sensor. The board does not have one, so it
// is off by default and the capture says "not fitted", exactly as the board
// would. Turn it on only when you are going to say out loud that it is invented.
//
// Two jobs. It gives you a demo that needs no board at all, and it proves the
// C++ and the Python agree byte for byte about what a record is — a mismatch
// there is a whole evening lost on the bench.
#include "ac_node.h"
#include "ac_gateway.h"
#include "ac_sim.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <ctime>

using namespace ac;

static const char* kKey = "annachain-dev-key-not-for-field";

// A link that writes the protocol to stdout instead of to a radio.
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

// Where the simulated trip starts. By default it ends at the moment the capture
// is made, because the server refuses readings older than a node could have
// held them (backend/checks.py, check 5). --start pins it for a reproducible run.
static uint32_t tripStart(long explicitStart, int readings) {
  if (explicitStart > 0) return (uint32_t)explicitStart;
  uint32_t now = (uint32_t)time(nullptr);
  uint32_t start = now - (uint32_t)readings * 300u;
  return start - start % 300u;          // align to the 5-minute sampling grid
}

static void printKey() {
  std::printf("K %u ", 0x26232001u);
  for (const char* p = kKey; *p; ++p) std::printf("%02x", (unsigned char)*p);
  std::printf("\n");
}

// The real data path: the node never talks to the server, only to the gateway.
static int viaGateway(int online1, int dark, bool ethylene, long start) {
  const int hold = 12;                 // readings the cab stays dark after the crate is back
  SimClock   clk(tripStart(start, online1 + dark + hold));
  SimSensors sns(7);
  sns.setEthyleneFitted(ethylene);
  MemStore   store(4096);
  SoftSigner signer(kKey);
  SimRadio   radio;
  GwBuffer   gbuf(8192);
  DumpLink   up;                       // the gateway's uplink: this is what gets printed
  Gateway    gw(0xAA000001, clk, radio, gbuf, up);
  SimNodeToGateway lora(radio);
  lora.attach(&gw);                    // the node asks the gateway what the server has
  Node node(0x26232001, clk, sns, store, lora, signer);
  gw.begin(); gw.setBatchSize(20);
  node.setBatchSize(20); node.begin();
  printKey();

  for (int i = 0; i < online1; ++i) {
    if (i == online1 * 55 / 100) sns.openDoor(35);
    node.tick(); gw.poll(); gw.forward();
    clk.advance(300);
  }

  std::printf("# offline\n");
  lora.setUp(false);                   // the crate is out of LoRa range...
  up.setUp(false);                     // ...and the cab has no mobile signal
  for (int i = 0; i < dark; ++i) { node.tick(); gw.poll(); clk.advance(300); }

  lora.setUp(true);                    // back in range: gap notice, then the backlog
  node.resync();
  for (int i = 0; i < hold; ++i) { node.tick(); gw.poll(); clk.advance(300); }
  std::fprintf(stderr, "gateway holding %u frames; gap notices heard: %u\n",
               gw.buffered(), gw.stats().gapsReceived);

  up.setUp(true);                      // signal at the cab
  std::printf("# online\n");
  gw.forward();
  std::printf("# done\n");

  const GwStats& g = gw.stats();
  std::fprintf(stderr,
      "via gateway: node stored %u, declared %u gap(s); gateway forwarded %u records "
      "and %u gap notice(s), dropped %u records and %u notices\n",
      node.stats().stored, node.stats().gapsDeclared, g.forwarded, g.gapsForwarded,
      g.dropped, g.gapsDropped);
  return (g.gapsDropped || g.dropped || gw.buffered()) ? 1 : 0;
}

int main(int argc, char** argv) {
  int online1 = 1000, dark = 350;
  bool ethylene = false, gateway = false;
  long start = 0;
  int pos = 0;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "--ethylene")) ethylene = true;
    else if (!strcmp(argv[i], "--gateway")) gateway = true;
    else if (!strcmp(argv[i], "--start") && i + 1 < argc) start = atol(argv[++i]);
    else if (pos == 0) { online1 = atoi(argv[i]); pos++; }
    else if (pos == 1) { dark    = atoi(argv[i]); pos++; }
  }

  if (gateway) return viaGateway(online1, dark, ethylene, start);

  SimClock   clk(tripStart(start, online1 + dark));
  SimSensors sns(7);
  sns.setEthyleneFitted(ethylene);
  MemStore   store(4096);
  SoftSigner signer(kKey);
  DumpLink   link;
  Node node(0x26232001, clk, sns, store, link, signer);
  node.setBatchSize(20);
  node.begin();

  printKey();

  // A real trip is not a flat line. Somewhere before the blind spot a door is
  // opened at a checkpoint, the load warms, ethylene starts climbing, and the
  // battery slowly falls. Without an incident there is nothing for the alert
  // rules to catch, and a demo where nothing ever goes wrong proves nothing.
  for (int i = 0; i < online1; ++i) {
    if (i == online1 * 55 / 100) sns.openDoor(35);      // checkpoint, door open
    if (i == online1 * 78 / 100) sns.setTamper(true);   // someone opens the box
    if (i == online1 * 80 / 100) sns.setTamper(false);
    sns.setBattery((uint8_t)(100 - (i * 22) / (online1 + dark)));
    node.tick();
    clk.advance(300);
  }

  // Markers so a replay tool knows where the blind spot was. The node itself
  // sends nothing at all in here, which is exactly the point.
  std::printf("# offline\n");
  link.setUp(false);
  for (int i = 0; i < dark;   ++i) {
    sns.setBattery((uint8_t)(100 - ((online1 + i) * 22) / (online1 + dark)));
    if (i == dark / 2) sns.setFaulty(true);             // the sensor drops out
    if (i == dark / 2 + 3) sns.setFaulty(false);        // and comes back
    node.tick();
    clk.advance(300);
  }
  link.setUp(true);
  std::printf("# online\n");
  node.resync();
  std::printf("# done\n");

  std::fprintf(stderr, "dumped %u records (%d online, %d through the outage)\n",
               node.stats().stored, online1, dark);
  std::fprintf(stderr, ethylene
      ? "ethylene: SIMULATED (--ethylene). The board has no ethylene sensor.\n"
      : "ethylene: not fitted, as on the real board\n");
  return 0;
}
