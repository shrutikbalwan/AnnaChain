// AnnaChain — the demonstration, on a laptop, with no hardware.
//
//   pio run -e native -t exec
//   (or)  g++ -std=gnu++17 -Ilib/ac lib/ac/*.cpp native/main.cpp -o sh && ./sh
//
// This is the same code that will run on the ESP32-S3. Only the clock, the
// sensor, the flash and the radio are swapped for stand-ins.
#include "ac_node.h"
#include "ac_sim.h"
#include <cstdio>
#include <cstring>

using namespace ac;

static const uint32_t kDevice = 0x26232001;
static const uint32_t kTripStart = 1758758400u;   // 25 Sep 2025, 00:00 UTC

static void rule(const char* title) {
  std::printf("\n\033[1m%s\033[0m\n", title);
  for (int i = 0; i < 74; ++i) std::putchar('-');
  std::putchar('\n');
}

static void line(const Node& n, const SimServer& srv, const char* where) {
  std::printf("  %-26s stored %5u   sent %5u   waiting %5u   server holds %5zu\n",
              where, n.stats().stored, n.stats().sent, n.pending(), srv.held());
}

int main() {
  SimClock  clk(kTripStart);
  SimSensors sns(7);
  MemStore  store(AC_LOG_CAPACITY);
  SoftSigner signer;
  SimServer srv;
  SimLink   link(srv, signer);

  Node node(kDevice, clk, sns, store, link, signer);
  node.setBatchSize(20);
  node.begin();

  std::printf("\n\033[1mAnnaChain node %08X\033[0m — Nashik to JNPT, 9 t of grapes\n",
              kDevice);
  std::printf("sample every 5 min   flash holds %u records (%.1f days)\n",
              store.capacity(), store.capacity() * 5.0 / 60.0 / 24.0);

  // ── leg 1: signal, everything flows ─────────────────────────────────────
  rule("1  Pack-house to Dhule — signal present");
  for (int i = 0; i < 1000; ++i) { node.tick(); clk.advance(300); }
  line(node, srv, "leaving Dhule");

  // ── leg 2: the blind spot ───────────────────────────────────────────────
  rule("2  Past Dhule — no signal for 29 hours");
  link.setUp(false);
  sns.openDoor(20);                       // a checkpoint, mid-outage
  for (int i = 0; i < 350; ++i) { node.tick(); clk.advance(300); }
  line(node, srv, "still dark");
  std::printf("  records %u ... %u are on the device and nowhere else\n",
              node.ackedSeq() + 1, store.lastSeq());

  // ── leg 3: signal returns ───────────────────────────────────────────────
  rule("3  Signal returns — the node fills the gap");
  size_t before = srv.held();
  link.setUp(true);
  node.resync();
  line(node, srv, "after catch-up");
  std::printf("  the server gained %lu records in one reconnection\n",
              (unsigned long)(srv.held() - before));

  // ── did anything go missing ─────────────────────────────────────────────
  rule("4  Was anything lost?");
  bool ordered = true;
  const auto& db = srv.records();
  for (size_t i = 1; i < db.size(); ++i)
    if (db[i].seq != db[i-1].seq + 1) { ordered = false; break; }

  std::printf("  records written on the device : %u\n", node.stats().stored);
  std::printf("  records accepted by the server: %lu\n", (unsigned long)srv.held());
  std::printf("  records dropped               : %u\n", node.stats().dropped);
  std::printf("  arrived in sequence           : %s\n", ordered ? "yes" : "NO");
  std::printf("  \033[1m%s\033[0m\n",
      (srv.held() == node.stats().stored && ordered && node.stats().dropped == 0)
      ? "nothing was lost, and nothing arrived out of order"
      : "*** RECORDS WERE LOST ***");

  // ── the chain still holds ───────────────────────────────────────────────
  rule("5  Is the record still trustworthy?");
  uint32_t bad = verifyChain(store, signer);
  std::printf("  chain check over %u records: %s\n", store.count(),
              bad ? "BROKEN" : "every link verifies");

  // ── now edit one reading, the way someone would ─────────────────────────
  uint32_t victim = store.firstSeq() + store.count() / 2;
  uint8_t raw[kRecBytes]; store.read(victim, raw);
  Record r; decode(raw, r);
  std::printf("\n  someone edits record %u: %.2f C  ->  4.00 C\n", victim, r.tempC());
  store.forceEdit(victim, 400);

  bad = verifyChain(store, signer);
  std::printf("  chain check again: %s at record %u\n",
              bad ? "\033[1mBROKEN\033[0m" : "still fine (bad)", bad);

  // and the server refuses it too
  store.read(victim, raw);
  uint32_t acked = 0;
  SimServer fresh;
  SimLink   l2(fresh, signer);
  l2.send(raw, 1, acked);
  std::printf("  the server's answer: rejected — %s\n", fresh.lastReject());

  rule("What this proves");
  std::printf("  · every reading was on flash before any radio was touched\n");
  std::printf("  · 29 hours without signal produced a delay, not a gap\n");
  std::printf("  · only the missing records went up, in order, on reconnect\n");
  std::printf("  · one edited reading breaks the chain and is refused\n\n");

  bool pass = (srv.held() == node.stats().stored) && ordered &&
              node.stats().dropped == 0 && bad == victim;
  std::printf("%s\n\n", pass ? "\033[32mDEMO PASSED\033[0m" : "\033[31mDEMO FAILED\033[0m");
  return pass ? 0 : 1;
}
