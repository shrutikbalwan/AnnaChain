// AnnaChain — self-tests. Plain asserts, no framework, runs anywhere.
//
//   pio run -e test -t exec
//   (or)  g++ -std=gnu++17 -Ilib/ac lib/ac/*.cpp tools/selftest.cpp -o t && ./t
//
// These are the claims the deck makes. If one of them ever goes red, the deck
// is wrong, not the test.
#include "ac_node.h"
#include "ac_gateway.h"
#include "ac_sim.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <string>

using namespace ac;

static int failures = 0, checks = 0;
#define CHECK(cond, what)                                                   \
  do {                                                                      \
    checks++;                                                               \
    if (!(cond)) { failures++;                                              \
      std::printf("  \033[31mFAIL\033[0m  %s   (%s:%d)\n", what,            \
                  __FILE__, __LINE__); }                                    \
    else std::printf("  \033[32mok\033[0m    %s\n", what);                  \
  } while (0)

static void head(const char* s) { std::printf("\n%s\n", s); }

// ── 1. the hash is really SHA-256 ─────────────────────────────────────────
static void test_sha256() {
  head("SHA-256 against the published vectors");
  uint8_t d[32]; char hex[65];

  Sha256::hash((const uint8_t*)"abc", 3, d); hex32(d, hex);
  CHECK(!strcmp(hex, "ba7816bf8f01cfea414140de5dae2223"
                     "b00361a396177a9cb410ff61f20015ad"), "\"abc\"");

  Sha256::hash((const uint8_t*)"", 0, d); hex32(d, hex);
  CHECK(!strcmp(hex, "e3b0c44298fc1c149afbf4c8996fb924"
                     "27ae41e4649b934ca495991b7852b855"), "empty string");

  const char* m = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
  Sha256::hash((const uint8_t*)m, strlen(m), d); hex32(d, hex);
  CHECK(!strcmp(hex, "248d6a61d20638b8e5c026930c3e6039"
                     "a33ce45964ff2167f6ecedd419db06c1"), "56-byte message");

  // a message that crosses several blocks
  std::string big(1000, 'a');
  Sha256::hash((const uint8_t*)big.data(), big.size(), d); hex32(d, hex);
  CHECK(!strcmp(hex, "41edece42d63e8d9bf515a9ba6932e1c"
                     "20cbc9f5a5d134645adb5db1b9737ea3"), "1000 x 'a'");
}

// ── 2. a record survives the round trip byte for byte ─────────────────────
static void test_record() {
  head("Record encoding");
  Record a;
  a.device = 0x26232001; a.seq = 4242; a.ts = 1758758400u;
  a.temp = -1875; a.rh = 9123; a.c2h4 = 47; a.flags = FLAG_TAMPER | FLAG_COLD;
  a.batt = 63;
  for (int i = 0; i < 32; ++i) { a.prev[i] = (uint8_t)(i * 7); a.sig[i] = (uint8_t)(255 - i); }

  uint8_t raw[kRecBytes]; encode(a, raw);
  Record b; decode(raw, b);

  CHECK(b.seq == a.seq && b.ts == a.ts, "sequence and timestamp survive");
  CHECK(b.temp == a.temp, "a sub-zero temperature survives (signed)");
  CHECK(b.rh == a.rh && b.c2h4 == a.c2h4, "humidity and ethylene survive");
  CHECK(b.flags == a.flags && b.batt == a.batt, "flags and battery survive");
  CHECK(!memcmp(b.prev, a.prev, 32) && !memcmp(b.sig, a.sig, 32), "hashes survive");
  CHECK(b.temp == -1875 && b.tempC() < -18.0, "-18.75 C reads back as -18.75 C");
}

// ── 2b. the v1 layout is frozen, and told apart by its length ────────────
// docs/CRYPTO.md. The same record, as bytes, is in
// backend/tests/test_record_format.py: if either side's encoder changes, one of
// the two suites goes red.
static void test_record_format() {
  head("The v1 record format is frozen, and has no version byte");
  static const char* golden =
      "012023260700000080b9b56aadf8a3232f00093f00070e151c232a31383f464d545b"
      "626970777e858c939aa1a8afb6bdc4cbd2d99b3d31af2a558f4021ac074f5f111202"
      "114de7378ea36521c4f47f3e7dc97f32";
  Record a;
  a.device = 0x26232001; a.seq = 7; a.ts = 1790294400u;
  a.temp = -1875; a.rh = 9123; a.c2h4 = 47; a.flags = FLAG_TAMPER | FLAG_COLD;
  a.batt = 63;
  for (int i = 0; i < 32; ++i) a.prev[i] = (uint8_t)(i * 7);
  SoftSigner s("annachain-test-key-node-a-000000");
  uint8_t d[32]; digest(a, d); s.sign(d, a.sig);
  uint8_t raw[kRecBytes]; encode(a, raw);
  std::string hex;
  for (size_t i = 0; i < kRecBytes; ++i) {
    char b[3]; std::snprintf(b, sizeof b, "%02x", raw[i]); hex += b;
  }
  CHECK(hex == golden, "a v1 record is byte for byte the one the server's tests hold");
  CHECK(recordFormat(raw, kRecBytes) == kFormatV1, "84 bytes is v1, with no version byte");

  a.device = 0x26232002;                    // node B: its first byte is 0x02
  encode(a, raw);
  CHECK(raw[0] == 2 && recordFormat(raw, kRecBytes) == kFormatV1,
        "a v1 record whose first byte is 2 is still v1: length decides, not byte 0");

  uint8_t v2[117] = {2};
  CHECK(recordFormat(v2, sizeof v2) == 0, "a later format is not mistaken for v1");
}

// ── a small rig, so each test starts clean ────────────────────────────────
struct Rig {
  SimClock   clk{1758758400u};
  SimSensors sns{3};
  MemStore   store{4096};
  SoftSigner signer;
  SimServer  srv;
  SimLink    link{srv, signer};
  Node       node{0x26232001, clk, sns, store, link, signer};
  Rig() { node.setBatchSize(20); node.begin(); }
  void run(int n) { for (int i = 0; i < n; ++i) { node.tick(); clk.advance(300); } }
};

// ── 3. nothing leaves before it is stored ─────────────────────────────────
static void test_store_first() {
  head("Store first, transmit second");
  Rig r;
  r.link.setUp(false);                       // no radio at all, from the start
  r.run(120);
  CHECK(r.node.stats().stored == 120, "120 readings stored with the radio dead");
  CHECK(r.srv.held() == 0, "the server received nothing, as expected");
  CHECK(verifyChain(r.store, r.signer) == 0, "the chain is whole anyway");
}

// ── 4. the claim on the deck ──────────────────────────────────────────────
static void test_outage_gap_fill() {
  head("29 hours dark, then catch up (slide 5)");
  Rig r;
  r.run(1000);
  CHECK(r.srv.held() == 1000, "1000 records delivered while online");

  r.link.setUp(false);
  r.run(350);
  CHECK(r.srv.held() == 1000, "server still holds 1000 during the outage");
  CHECK(r.node.pending() == 350, "350 records waiting on the device");

  r.link.setUp(true);
  r.node.resync();
  CHECK(r.srv.held() == 1350, "all 350 arrive on reconnect");
  CHECK(r.node.pending() == 0, "nothing left waiting");
  CHECK(r.node.stats().dropped == 0, "nothing was dropped");

  bool ordered = true;
  const auto& db = r.srv.records();
  for (size_t i = 1; i < db.size(); ++i)
    if (db[i].seq != db[i-1].seq + 1) ordered = false;
  CHECK(ordered, "they arrived in sequence, not in a heap");
}

// ── 5. the link dies halfway through the catch-up ─────────────────────────
static void test_link_dies_mid_catchup() {
  head("The link dies in the middle of the catch-up");
  Rig r;
  r.run(100);
  r.link.setUp(false);
  r.run(200);

  r.link.setUp(true);
  r.link.dropNextSend();                      // dies on the first batch
  r.node.resync();
  CHECK(r.srv.held() < 300, "the catch-up stopped where the link died");
  CHECK(r.node.pending() > 0, "the rest is still held on the device");

  r.link.setUp(true);                         // signal comes back again
  r.node.resync();
  CHECK(r.srv.held() == 300, "the second attempt finishes the job");
  CHECK(r.node.pending() == 0, "nothing lost to the interrupted batch");
}

// ── 6. the node reboots mid-outage ────────────────────────────────────────
static void test_reboot_mid_outage() {
  head("Power cycle in the middle of the outage");
  Rig r;
  r.run(50);
  r.link.setUp(false);
  r.run(100);

  // A reboot: same flash, brand new Node object, nothing carried in RAM.
  Node again(0x26232001, r.clk, r.sns, r.store, r.link, r.signer);
  again.setBatchSize(20);
  CHECK(again.begin(), "the node comes back up");
  CHECK(again.ackedSeq() == 50, "it remembers the server had 50");

  for (int i = 0; i < 100; ++i) { again.tick(); r.clk.advance(300); }
  CHECK(r.store.lastSeq() == 250, "the sequence continued instead of restarting");
  CHECK(verifyChain(r.store, r.signer) == 0, "the chain survived the reboot");

  r.link.setUp(true);
  again.resync();
  CHECK(r.srv.held() == 250, "everything from both sides of the reboot arrives");
}

// ── 7. an edited reading is caught ────────────────────────────────────────
static void test_tamper() {
  head("Someone edits a stored reading");
  Rig r;
  r.run(200);
  CHECK(verifyChain(r.store, r.signer) == 0, "chain is clean to begin with");

  r.store.forceEdit(120, 400);
  uint32_t bad = verifyChain(r.store, r.signer);
  CHECK(bad == 120, "the chain breaks at exactly the edited record");

  uint8_t raw[kRecBytes]; r.store.read(120, raw);
  SimServer fresh; SimLink l2(fresh, r.signer);
  uint32_t acked = 0;
  l2.send(raw, 1, acked);
  CHECK(fresh.held() == 0, "the server refuses to store it");
  CHECK(!strcmp(fresh.lastReject(), "bad signature"), "and says why: bad signature");
}

// ── 8. a replayed record is caught ────────────────────────────────────────
static void test_replay() {
  head("Someone replays an old record");
  Rig r;
  r.run(50);
  uint8_t raw[kRecBytes]; r.store.read(10, raw);     // a record the server already has
  uint32_t acked = 0;
  r.link.send(raw, 1, acked);
  CHECK(r.srv.rejected() > 0, "the replay is rejected");
  CHECK(!strcmp(r.srv.lastReject(), "duplicate or replay"), "and named as a replay");
  CHECK(r.srv.held() == 50, "the database is unchanged");
}

// ── 9. the ring wraps, and says so ────────────────────────────────────────
static void test_ring_wrap() {
  head("A very long outage — longer than the flash");
  SimClock clk{1758758400u};
  SimSensors sns{5};
  MemStore store{500};                            // a deliberately tiny flash
  SoftSigner signer; SimServer srv; SimLink link{srv, signer};
  Node node{0x26232001, clk, sns, store, link, signer};
  node.begin();

  link.setUp(false);
  for (int i = 0; i < 700; ++i) { node.tick(); clk.advance(300); }

  CHECK(store.count() == 500, "flash holds its 500 newest records");
  CHECK(store.firstSeq() == 201 && store.lastSeq() == 700, "the oldest 200 rolled off");
  CHECK(node.stats().dropped == 200, "the node reports the 200 it could not keep");

  link.setUp(true);
  node.resync();
  CHECK(srv.held() == 500, "it uploads everything it still has");
  CHECK(node.stats().gapsDeclared == 1, "and declares the hole rather than hiding it");
  CHECK(srv.gaps().size() == 1 && srv.gaps()[0].from == 1 && srv.gaps()[0].to == 200,
        "the server has records 1-200 on file as missing");
  CHECK(verifyChain(store, signer) == 0, "and what it has still verifies");
}

// ── 9b. a gap notice has to come from the device ─────────────────────────
// A link that keeps the notice instead of delivering it, so the test can look
// at exactly what the node signed.
struct CaptureGapLink : public ILink {
  bool up() override { return true; }
  bool queryLastAck(uint32_t, uint32_t& a) override { a = 0; return true; }
  bool send(const uint8_t*, size_t, uint32_t& acked) override { acked = 0; return false; }
  bool declareGap(uint32_t d, uint32_t f, uint32_t t, const uint8_t mac[32]) override {
    dev = d; from = f; to = t; memcpy(this->mac, mac, 32); n++;
    return false;                 // say no, so the node does not carry on sending
  }
  uint32_t dev = 0, from = 0, to = 0, n = 0;
  uint8_t mac[32] = {0};
};

static void test_gap_notice_signed() {
  head("A gap notice is signed by the device");
  SimClock clk{1758758400u};
  SimSensors sns{5};
  MemStore store{100};
  SoftSigner signer("gap-test-device-key");
  CaptureGapLink link;
  Node node{0x26232001, clk, sns, store, link, signer};
  node.begin();
  for (int i = 0; i < 150; ++i) { node.tick(); clk.advance(300); }   // ring wraps

  CHECK(link.n >= 1 && link.from == 1 && link.to == 50,
        "the node declares records 1-50 lost");
  uint8_t d[32];
  gapDigest(link.dev, link.from, link.to, d);
  CHECK(signer.verify(d, link.mac), "the notice verifies against the device key");

  gapDigest(link.dev, link.from, link.to + 1, d);
  CHECK(!signer.verify(d, link.mac), "the same signature does not cover a bigger hole");

  SoftSigner other("someone-else");
  gapDigest(link.dev, link.from, link.to, d);
  CHECK(!other.verify(d, link.mac), "and another key cannot have produced it");

  // A notice nobody signed is refused, and the server's record is unchanged.
  SimServer srv; SimLink sl{srv, signer};
  uint8_t forged[32] = {0};
  CHECK(!sl.declareGap(0x26232001, 1, 50, forged) && srv.gaps().empty(),
        "an unsigned gap notice is refused and nothing is recorded");
  CHECK(sl.declareGap(0x26232001, 1, 50, link.mac) && srv.gaps().size() == 1,
        "the device's own notice is accepted");
}

// ── 9c. the clock starts where the comment says it does ──────────────────
static void civil(uint32_t t, int& y, unsigned& m, unsigned& d) {
  // days since 1970-01-01 to a calendar date (Howard Hinnant's algorithm)
  long z = (long)(t / 86400) + 719468;
  long era = z / 146097;
  unsigned doe = (unsigned)(z - era * 146097);
  unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
  unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
  unsigned mp = (5 * doy + 2) / 153;
  d = doy - (153 * mp + 2) / 5 + 1;
  m = mp < 10 ? mp + 3 : mp - 9;
  y = (int)(yoe + era * 400) + (m <= 2);
}

static void test_clock_base() {
  head("The board's clock starts on the date it claims");
  int y; unsigned m, d;
  civil(kClockBase, y, m, d);
  CHECK(y == 2026 && m == 9 && d == 25, "kClockBase is 25 Sep 2026");
  CHECK(kClockBase % 86400 == 0, "at 00:00 UTC");
}

// ── 10. a sensor that stops answering ─────────────────────────────────────
static void test_sensor_fault() {
  head("The sensor stops answering");
  Rig r;
  r.run(20);
  r.sns.setFaulty(true);
  r.run(20);
  CHECK(r.node.stats().sensorBad == 20, "20 bad readings counted, not hidden");
  uint8_t raw[kRecBytes]; r.store.read(r.store.lastSeq(), raw);
  Record rec; decode(raw, rec);
  CHECK(rec.flags & FLAG_SENSORBAD,
        "the record is flagged \u2014 a dead sensor is not a 0.00 C reading");
  CHECK(r.node.stats().stored == 40, "records are still written — a gap would be worse");
  CHECK(verifyChain(r.store, r.signer) == 0, "the chain is still unbroken");
}

// ── 11. the gateway: a buffer, not an authority ──────────────────────────
struct TruckRig {
  SimClock   clk{1758758400u};
  SimRadio   radio;
  GwBuffer   gbuf{2000};
  SoftSigner signer;
  SimServer  srv;
  SimLink    uplink{srv, signer};
  Gateway    gw{0xAA0001, clk, radio, gbuf, uplink};

  // three crate nodes, all talking to the gateway over the same radio
  SimSensors s1{1}, s2{2}, s3{3};
  MemStore   m1{4096}, m2{4096}, m3{4096};
  SimNodeToGateway l1{radio}, l2{radio}, l3{radio};
  Node n1{0x1001, clk, s1, m1, l1, signer};
  Node n2{0x1002, clk, s2, m2, l2, signer};
  Node n3{0x1003, clk, s3, m3, l3, signer};

  TruckRig() {
    gw.begin(); gw.setBatchSize(20);
    n1.begin(); n2.begin(); n3.begin();
  }
  void run(int ticks, bool pollGw = true) {
    for (int i = 0; i < ticks; ++i) {
      n1.tick(); n2.tick(); n3.tick();
      if (pollGw) { gw.poll(); gw.forward(); }
      clk.advance(300);
    }
  }
};

static void test_gateway_basic() {
  head("The truck gateway collects from three nodes");
  TruckRig r;
  r.run(30);
  CHECK(r.gw.stats().nodes == 3, "it heard from all three crate nodes");
  CHECK(r.gw.stats().received == 90, "90 records came in over LoRa");
  CHECK(r.gw.stats().forwarded == 90, "and all 90 went upstream");
  CHECK(r.gw.buffered() == 0, "nothing left in the gateway buffer");
  CHECK(r.srv.held() == 90, "the server has all of them");
}

static void test_gateway_buffers_when_uplink_dies() {
  head("The uplink dies but the trailer keeps talking");
  TruckRig r;
  r.run(10);
  size_t before = r.srv.held();

  r.uplink.setUp(false);          // no mobile signal at the cab
  r.run(20);
  CHECK(r.srv.held() == before, "the server received nothing more");
  CHECK(r.gw.buffered() == 60, "the gateway is holding all 60 records");
  CHECK(r.gw.stats().dropped == 0, "and has dropped none of them");

  r.uplink.setUp(true);
  r.gw.forward();
  CHECK(r.gw.buffered() == 0, "the buffer drains when signal returns");
  CHECK(r.srv.held() == before + 60, "every held record reached the server");
}

static void test_gateway_nothing_leaves_early() {
  head("The uplink dies in the middle of a batch");
  TruckRig r;
  r.run(10, false);               // nodes talk, gateway does not forward yet
  r.gw.poll();
  CHECK(r.gw.buffered() == 30, "30 records waiting");

  r.uplink.dropNextSend();        // the send fails partway
  r.gw.forward();
  CHECK(r.gw.buffered() == 30, "nothing was thrown away by the failed send");

  r.uplink.setUp(true);
  r.gw.forward();
  CHECK(r.gw.buffered() == 0, "the retry clears it");
  CHECK(r.srv.held() == 30, "and the server has exactly 30");
}

static void test_gateway_duplicates() {
  head("A node repeats itself because it missed an acknowledgement");
  TruckRig r;
  r.run(5, false);
  r.gw.poll();
  uint32_t first = r.gw.stats().received;

  uint8_t rec[kRecBytes];
  r.m1.read(3, rec);
  r.radio.transmit(rec);          // the same record, heard a second time
  r.radio.transmit(rec);
  r.gw.poll();

  CHECK(r.gw.stats().received == first + 2, "both repeats were heard");
  CHECK(r.gw.stats().duplicates == 2, "and both were recognised as duplicates");
  CHECK(r.gw.buffered() == first, "neither was queued for the uplink");
}

static void test_gateway_overrun() {
  head("A very long outage overruns the gateway buffer");
  SimClock clk{1758758400u};
  SimRadio radio;
  GwBuffer gbuf{40};              // deliberately tiny
  SoftSigner signer;
  SimServer srv;
  SimLink uplink{srv, signer};
  Gateway gw{0xAA0001, clk, radio, gbuf, uplink};
  gw.begin();

  SimSensors sns{4}; MemStore mem{4096}; SimNodeToGateway link{radio};
  Node node{0x1001, clk, sns, mem, link, signer};
  node.begin();

  uplink.setUp(false);
  for (int i = 0; i < 100; ++i) { node.tick(); gw.poll(); clk.advance(300); }

  CHECK(gw.buffered() == 40, "the gateway holds its 40 newest records");
  CHECK(gw.stats().dropped == 60, "and reports the 60 it could not keep");
  CHECK(mem.count() == 100, "but the node still has all 100 in its own flash");
}

// ── 12. a declared gap reaches the server through the gateway ────────────
// On the truck the path is node -> LoRa -> gateway -> server. A gap notice that
// only works when the node talks to the server directly would pass here and
// fail in the field.
struct GapRig {
  SimClock   clk{1758758400u};
  SimRadio   radio;
  GwBuffer   gbuf;
  SoftSigner signer{"gateway-gap-test-device-key"};
  SimServer  srv;
  SimLink    uplink{srv, signer};
  Gateway    gw{0xAA0002, clk, radio, gbuf, uplink};
  SimSensors sns{9};
  MemStore   mem{100};                     // a tiny flash, so it overruns
  SimNodeToGateway link{radio};
  Node       node{0x2001, clk, sns, mem, link, signer};

  explicit GapRig(uint32_t gwCap = 2000) : gbuf(gwCap) {
    gw.begin(); gw.setBatchSize(20); node.setBatchSize(20); node.begin();
  }
  // The crate is out of LoRa range for n readings: the node logs, the gateway
  // hears nothing.
  void outOfRange(int n) {
    link.setUp(false);
    for (int i = 0; i < n; ++i) { node.tick(); gw.poll(); clk.advance(300); }
    link.setUp(true);
  }
  void expectedMac(uint32_t from, uint32_t to, uint8_t mac[32]) {
    uint8_t d[32]; gapDigest(0x2001, from, to, d); signer.sign(d, mac);
  }
};

static void test_gateway_relays_gap() {
  head("A declared gap crosses the gateway");
  GapRig r;
  r.uplink.setUp(false);                   // and no mobile signal at the cab either
  r.outOfRange(150);                       // flash holds 100: records 1-50 are gone
  r.node.resync();                         // back in range: gap notice, then 51-150
  r.gw.poll();

  CHECK(r.gw.stats().gapsReceived == 1, "the gateway heard the node's gap notice");
  uint8_t frame[kRecBytes], want[kRecBytes], mac[32];
  uint8_t kind = 0xFF;
  r.expectedMac(1, 50, mac);
  encodeGapFrame(0x2001, 1, 50, mac, want);
  CHECK(r.gbuf.peekAt(0, frame, kind) && kind == FRAME_GAP && !memcmp(frame, want, kRecBytes),
        "it is buffered first, byte for byte as the node signed it");
  CHECK(r.gw.buffered() == 101, "ahead of the 100 records that survived");

  r.gw.forward();                          // uplink still down
  CHECK(r.srv.gaps().empty() && r.gw.buffered() == 101,
        "with no uplink it is held, not dropped");

  r.uplink.setUp(true);
  r.gw.forward();
  CHECK(r.srv.gaps().size() == 1 && r.srv.gaps()[0].from == 1 && r.srv.gaps()[0].to == 50,
        "when the uplink returns the server records 1-50 as lost");
  CHECK(!memcmp(r.srv.gaps()[0].mac, mac, 32), "with the node's signature unchanged");
  uint8_t d[32]; gapDigest(0x2001, 1, 50, d);
  CHECK(r.signer.verify(d, r.srv.gaps()[0].mac), "which verifies against the device key");
  CHECK(r.srv.held() == 100 && r.srv.lastAck(0x2001) == 150,
        "and every surviving record is accepted after it");
  CHECK(r.gw.stats().gapsForwarded == 1 && r.gw.buffered() == 0, "nothing left behind");
}

static void test_gateway_cannot_alter_gap() {
  head("The gateway cannot alter a gap notice");
  GapRig r;
  r.uplink.setUp(false);
  r.outOfRange(150);
  r.node.resync();
  r.gw.poll();
  uint8_t frame[kRecBytes]; uint8_t kind = 0;
  r.gbuf.peekAt(0, frame, kind);

  // A compromised gateway widens the hole, to swallow readings it wants gone.
  uint32_t dev, from, to; uint8_t mac[32];
  decodeGapFrame(frame, dev, from, to, mac);
  SimServer fresh; SimLink direct{fresh, r.signer};
  CHECK(!direct.declareGap(dev, from, to + 20, mac) && fresh.gaps().empty(),
        "a widened gap is refused: the signature covers the range");
  mac[0] ^= 1;
  CHECK(!direct.declareGap(dev, from, to, mac) && fresh.gaps().empty(),
        "a notice with a changed signature is refused");
}

static void test_gateway_gap_overrun_is_counted() {
  head("A gap notice lost to gateway overrun is counted");
  GapRig r(30);                            // a gateway buffer smaller than the backlog
  r.uplink.setUp(false);
  r.outOfRange(150);
  r.node.resync();
  r.gw.poll();
  CHECK(r.gw.stats().gapsReceived == 1, "the notice arrived");
  CHECK(r.gw.stats().gapsDropped == 1, "and its loss is counted, not hidden");
  CHECK(r.gw.stats().dropped == 70, "alongside the 70 records that went with it");
}

static void test_gateway_cannot_forge() {
  head("The gateway cannot make a record up");
  TruckRig r;
  r.run(10, false);
  r.gw.poll();

  // Records must cross the gateway byte for byte.
  uint8_t fromNode[kRecBytes], atGateway[kRecBytes];
  r.m1.read(5, fromNode);
  bool found = false;
  for (uint32_t i = 0; i < r.gbuf.count(); ++i) {
    r.gbuf.peekAt(i, atGateway);
    if (!memcmp(atGateway, fromNode, kRecBytes)) { found = true; break; }
  }
  CHECK(found, "the record crossed the gateway unchanged, byte for byte");

  // Now let a compromised gateway try to improve a reading on its way past.
  uint8_t tampered[kRecBytes];
  memcpy(tampered, fromNode, kRecBytes);
  tampered[12] ^= 0x40;                     // nudge the temperature
  SimServer fresh; SimLink direct{fresh, r.signer};
  uint32_t acked = 0;
  direct.send(tampered, 1, acked);
  CHECK(fresh.held() == 0, "the server refuses the altered record");
  CHECK(!strcmp(fresh.lastReject(), "bad signature"),
        "because the gateway has no key, and the signature no longer matches");
}

int main() {
  std::printf("\n\033[1mAnnaChain self-tests\033[0m\n");
  test_sha256();
  test_record();
  test_record_format();
  test_store_first();
  test_outage_gap_fill();
  test_link_dies_mid_catchup();
  test_reboot_mid_outage();
  test_tamper();
  test_replay();
  test_ring_wrap();
  test_gap_notice_signed();
  test_clock_base();
  test_sensor_fault();
  test_gateway_basic();
  test_gateway_buffers_when_uplink_dies();
  test_gateway_nothing_leaves_early();
  test_gateway_duplicates();
  test_gateway_overrun();
  test_gateway_relays_gap();
  test_gateway_cannot_alter_gap();
  test_gateway_gap_overrun_is_counted();
  test_gateway_cannot_forge();

  std::printf("\n%d checks, %d failed\n", checks, failures);
  std::printf("%s\n\n", failures ? "\033[31mSOMETHING IS WRONG\033[0m"
                                 : "\033[32mALL GOOD\033[0m");
  return failures ? 1 : 0;
}
