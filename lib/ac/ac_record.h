// AnnaChain — one sensor record, and the chain it belongs to.
//
// A record is the unit of everything: it is built, hashed onto the previous
// one, signed inside the device, and only then written to flash. Nothing
// leaves the node that has not already been stored.
#pragma once
#include <stdint.h>
#include <stddef.h>

namespace ac {

// Wire layout, little-endian, fixed size so the flash log can be indexed by
// sequence number without a table of contents.
//   device   4
//   seq      4
//   ts       4
//   temp     2   deci-hundredths of a degree C, signed  (2350 = 23.50 C)
//   rh       2   hundredths of a percent                 (6512 = 65.12 %)
//   c2h4     2   parts per billion, 0xFFFF = sensor not fitted
//   flags    1
//   batt     1   percent
//   prev    32   SHA-256 of the previous record
//   sig     32   signature over the 52 bytes above
constexpr size_t kBodyBytes = 52;
constexpr size_t kRecBytes  = 84;

// The format version (docs/CRYPTO.md). The layout above is v1, and it has no
// version byte: every node, capture and database already holds these 84 bytes
// as evidence, so v1 is identified by its LENGTH, never by its first byte (the
// low byte of the device id, which can be anything). Every later format starts
// with a version byte of 2 or more, covered by its signature, and has a fixed
// length of its own that is never 84. Nothing here produces a later format.
constexpr uint8_t kFormatV1 = 1;

// The format of `len` bytes: kFormatV1, or 0 for a format this code does not know.
uint8_t recordFormat(const uint8_t* raw, size_t len);

enum Flags : uint8_t {
  FLAG_TAMPER   = 1 << 0,  // enclosure opened
  FLAG_MOVED    = 1 << 1,  // accelerometer above threshold
  FLAG_CHARGING = 1 << 2,  // solar input present
  FLAG_COLD     = 1 << 3,  // below 0 C — charging inhibited
  FLAG_SELFTEST  = 1 << 4,  // first record after power-up
  FLAG_SENSORBAD = 1 << 5,  // the sensor did not answer — this reading is not a measurement
  // A value in this record was invented by the simulator, not measured. Today
  // that means only the ethylene curve SimSensors makes up when told a sensor is
  // fitted (dump/fleet --ethylene); the board has none. Mock temperatures
  // (node_mock, dump, fleet) do NOT set it. Inside the signature, like every flag.
  FLAG_SIMULATED = 1 << 6,
  // The timestamp is uptime counted from kClockBase, not wall-clock time: the
  // record was taken before any server told this node the time. It is signed
  // and cannot be re-stamped, so it says so instead. Once a node's clock has
  // been set it never sets this again (Node::tick), and the server refuses a
  // flagged record from a device whose clock it has already seen set.
  // This is the LAST free bit: the flags byte is now full (docs/CRYPTO.md).
  FLAG_TIMEUNSET = 1 << 7,
};

constexpr uint16_t kEthyleneNotFitted = 0xFFFF;

struct Record {
  uint32_t device = 0;
  uint32_t seq    = 0;
  uint32_t ts     = 0;
  int16_t  temp   = 0;
  uint16_t rh     = 0;
  uint16_t c2h4   = kEthyleneNotFitted;
  uint8_t  flags  = 0;
  uint8_t  batt   = 0;
  uint8_t  prev[32] = {0};
  uint8_t  sig[32]  = {0};

  double tempC()    const { return temp / 100.0; }
  double humidity() const { return rh   / 100.0; }
};

// Serialise everything except the signature. This is what gets hashed and
// what gets signed — two different devices must produce identical bytes for
// identical readings, so the layout is explicit rather than a struct dump.
void encodeBody(const Record& r, uint8_t out[kBodyBytes]);
void encode(const Record& r, uint8_t out[kRecBytes]);
bool decode(const uint8_t in[kRecBytes], Record& out);

// SHA-256 over the body. This is the value the *next* record carries in prev[].
void digest(const Record& r, uint8_t out[32]);

// What a gap notice is signed over. A notice that records no longer exist is a
// claim about the record, so it has to come from the device just as a record
// does. Otherwise anyone who can reach the server can punch a hole in a
// consignment and call it an outage.
void gapDigest(uint32_t device, uint32_t from, uint32_t to, uint8_t out[32]);

// What travels over LoRa. A frame is always kRecBytes long so the gateway can
// buffer records and gap notices in one queue, in the order they must reach the
// server. The kind travels beside the frame, never inside a record.
// FRAME_QUERY is a node asking the gateway what the SERVER holds for it
// (device 4, then zeros); the answer goes back over the radio, never in a record.
enum FrameKind : uint8_t { FRAME_RECORD = 0, FRAME_GAP = 1, FRAME_QUERY = 2 };

// A gap notice as a frame, little-endian:
//   device 4 | from 4 | to 4 | mac 32 | zero 40
// mac is the device's signature over gapDigest(device, from, to). Anything that
// carries this frame can read it, and nothing that carries it can change it.
void encodeGapFrame(uint32_t device, uint32_t from, uint32_t to,
                    const uint8_t mac[32], uint8_t out[kRecBytes]);
void decodeGapFrame(const uint8_t in[kRecBytes], uint32_t& device, uint32_t& from,
                    uint32_t& to, uint8_t mac[32]);

}  // namespace ac
