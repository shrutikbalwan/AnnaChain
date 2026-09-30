// AnnaChain — the P1 gate: a board flashed today, run forward past 30 days.
//
//   g++ -std=gnu++17 -Ilib/ac lib/ac/ac_sha256.cpp lib/ac/ac_record.cpp \
//       lib/ac/ac_node.cpp lib/ac/ac_gateway.cpp lib/ac/ac_sim.cpp \
//       tools/clockgate.cpp -o clockgate && ./clockgate > gate.capture
//
// backend/tests/test_clock_gate.py builds this, and feeds what it prints to the
// real server (backend/app.py, checks.py) with the server's clock set to the
// simulated true time of each delivery. Every record must be accepted.
//
// The board is the one src/main.cpp builds, minus the hardware: its clock
// starts at kClockBase (this program's own build time) and counts uptime, and
// it runs 20 ppm fast, a typical crystal. True time starts now. The script:
//
//   day  0     flashed and powered up; no server for 2 days: records flagged
//   day  2     first contact: the server's time sets the clock
//   day 10-12  a 2-day outage: held, then delivered 2 days late
//   day 31     past 30 days of uptime on one boot (the old cliff was 30 days
//              after kClockBase, whatever the uptime)
//   day 32     the link drops; at 32.5 the board loses power for 2 hours,
//              reboots (uptime restarts at zero), and reaches the server at 33
//   day 36     end
//
// Output, one line each, in the order the server receives them:
//   K <device> <key-hex>
//   R <record-hex> <server-unix-time-at-delivery>
//   # <commentary>
#include "ac_node.h"
#include "ac_sim.h"
#include <cstdio>
#include <cstring>
#include <ctime>

using namespace ac;

static const uint32_t kDev = 0x2623C001;      // neither a demo id nor the board's (HIL.md step 1)
static const char*    kKey = "annachain-clock-gate-key-0000000";

// The server end of a USB or Wi-Fi link: answers with its last-ACK and its own
// clock, and writes down everything that reaches it and when.
class GateLink : public ILink {
 public:
  explicit GateLink(SimClock& truth) : truth_(truth) {}
  bool up() override { return up_; }
  bool queryLastAck(uint32_t, uint32_t& lastAck) override {
    timed_ = false;
    if (!up_) return false;
    lastAck = acked_;                 // the test asserts the server really took them
    timed_ = true;
    return true;
  }
  bool serverTime(uint32_t& t) override {
    if (!timed_) return false;
    t = truth_.now();
    return true;
  }
  bool send(const uint8_t* recs, size_t count, uint32_t& acked) override {
    if (!up_) return false;
    for (size_t i = 0; i < count; ++i) {
      const uint8_t* r = recs + i * kRecBytes;
      std::printf("R ");
      for (size_t b = 0; b < kRecBytes; ++b) std::printf("%02x", r[b]);
      std::printf(" %u\n", truth_.now());
      acked_ = (uint32_t)r[4] | (uint32_t)r[5] << 8 | (uint32_t)r[6] << 16 | (uint32_t)r[7] << 24;
    }
    acked = acked_;
    return true;
  }
  bool declareGap(uint32_t, uint32_t, uint32_t, const uint8_t*) override { return false; }
  void setUp(bool u) { up_ = u; }
 private:
  SimClock& truth_;
  bool up_ = true, timed_ = false;
  uint32_t acked_ = 0;
};

int main() {
  const uint32_t kStep = 300, kPerDay = 86400 / kStep;
  SimClock truth{(uint32_t)time(nullptr)};    // flashed today
  SimClock* board = new SimClock{kClockBase, false};
  MemStore store{4096};
  SimSensors sns{11};
  SoftSigner signer{kKey};
  GateLink link{truth};
  Node* node = new Node{kDev, *board, sns, store, link, signer};
  node->begin();

  char keyHex[65] = {0};
  for (int i = 0; i < 32; ++i) std::snprintf(keyHex + 2 * i, 3, "%02x", (uint8_t)kKey[i]);
  std::printf("K %u %s\n", kDev, keyHex);
  std::printf("# board clock starts at kClockBase = %u, true time %u (%lld s behind)\n",
              kClockBase, truth.now(), (long long)truth.now() - (long long)kClockBase);

  uint32_t fastTick = 0;
  for (uint32_t i = 0; i < 36 * kPerDay; ++i) {
    uint32_t day = i / kPerDay;
    bool down = day < 2 || (day >= 10 && day < 12) || day == 32;
    link.setUp(!down);
    if (i == 32 * kPerDay + kPerDay / 2) {
      // Power lost for 2 hours; the board comes back with uptime at zero.
      std::printf("# day 32.5: power off for 2 h, reboot\n");
      delete node; delete board;
      truth.advance(7200);
      board = new SimClock{kClockBase, false};
      node = new Node{kDev, *board, sns, store, link, signer};
      node->begin();
    }
    if (i == 2 * kPerDay) std::printf("# day 2: first contact\n");
    node->tick();
    truth.advance(kStep);
    board->advance(kStep);
    if (++fastTick == 167) { board->advance(1); fastTick = 0; }   // 20 ppm fast
  }
  std::printf("# %u records, the last at %u days of true time\n", store.lastSeq(), 36);
  delete node; delete board;
  return 0;
}
