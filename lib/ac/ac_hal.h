// AnnaChain — the four things the node needs from the world around it.
//
// Every one of these is an interface with two implementations: a fake one that
// runs on a laptop, and a real one that runs on the board. That is the whole
// reason you can build and demonstrate the logic before the parts arrive.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include "ac_record.h"

namespace ac {

// ── time ──────────────────────────────────────────────────────────────────
// Where a board's clock starts until the server sets it: 25 Sep 2026, 00:00 UTC.
// A node that ships with this wrong by a year stamps every reading a year old,
// which is what the server's timestamp check is there to catch. selftest
// checks that this constant really is the date written here.
constexpr uint32_t kClockBase = 1790294400u;

struct IClock {
  virtual ~IClock() {}
  virtual uint32_t now() = 0;            // unix seconds
  virtual void     sleep(uint32_t ms) = 0;
};

// ── sensors ───────────────────────────────────────────────────────────────
struct Reading {
  int16_t  temp  = 0;                    // hundredths of a degree C
  uint16_t rh    = 0;                    // hundredths of a percent
  uint16_t c2h4  = kEthyleneNotFitted;   // ppb
  uint8_t  batt  = 100;
  bool     tamper = false;
  bool     moved  = false;
  bool     charging = false;
  bool     ok = true;                    // false = the sensor did not answer
};

struct ISensors {
  virtual ~ISensors() {}
  virtual bool begin() = 0;
  virtual Reading read() = 0;
};

// ── the flash log ─────────────────────────────────────────────────────────
// A ring of fixed-size records, addressed by sequence number. When it wraps,
// the oldest record is the one that goes; the node keeps logging either way,
// which is the behaviour the problem statement actually asks for.
struct IStore {
  virtual ~IStore() {}
  virtual bool     begin() = 0;
  virtual bool     append(const uint8_t rec[kRecBytes]) = 0;
  virtual bool     read(uint32_t seq, uint8_t rec[kRecBytes]) = 0;
  virtual uint32_t firstSeq() const = 0;   // oldest still held, 0 if empty
  virtual uint32_t lastSeq()  const = 0;   // newest written, 0 if empty
  virtual uint32_t count()    const = 0;
  virtual uint32_t capacity() const = 0;
  virtual uint32_t loadAck() = 0;          // highest seq the server confirmed
  virtual bool     saveAck(uint32_t seq) = 0;
};

// ── the radio ─────────────────────────────────────────────────────────────
struct ILink {
  virtual ~ILink() {}
  virtual bool up() = 0;                   // is there a usable link right now
  // Ask the server what it already has. Returns false if the question could
  // not be asked — which is itself a dropped link, so the node keeps storing.
  virtual bool queryLastAck(uint32_t device, uint32_t& lastAck) = 0;
  // Send one batch. Returns the highest sequence number the server accepted.
  virtual bool send(const uint8_t* recs, size_t count, uint32_t& acked) = 0;
  // Tell the server, on the record, that records [from..to] no longer exist on
  // the device — the outage outlasted the flash. The server needs this, because
  // its sequence check would otherwise reject everything after the hole for
  // ever. A declared hole is auditable; a silent one is the thing the whole
  // project exists to prevent.
  //
  // mac is the device's signature over gapDigest(device, from, to). The server
  // refuses a notice it cannot verify: a declared hole needs the same standing
  // as a record, or anyone on the network can make one.
  virtual bool declareGap(uint32_t device, uint32_t from, uint32_t to,
                          const uint8_t mac[32]) = 0;
};

// ── the secure element ────────────────────────────────────────────────────
// SoftSigner today, ATECC608B when the chip is on the board. The private key
// never leaves the signer in either case, and the call site is identical.
struct ISigner {
  virtual ~ISigner() {}
  virtual bool begin() = 0;
  virtual bool sign(const uint8_t digest[32], uint8_t sig[32]) = 0;
  virtual bool verify(const uint8_t digest[32], const uint8_t sig[32]) = 0;
};

// ── the secure element, for real: ECDSA P-256 ─────────────────────────────
// An ECDSA signature is 64 bytes (r, s). The record format reserves 32 bytes
// for its signature and ISigner above produces 32, so the ATECC608B cannot
// simply replace SoftSigner. This interface exists so the chip's driver can be
// written, compiled and exercised on arrival day without pretending otherwise.
//
// Nothing signs records with it yet. Moving records onto it is a planned,
// separate change — a v2 record of 116 bytes, verified by the server and by
// the buyer's browser against a published public key — written up in
// docs/HIL.md, step 4.
struct IEcdsaSigner {
  virtual ~IEcdsaSigner() {}
  virtual bool begin() = 0;
  // The device's public key, X then Y, 32 bytes each.
  virtual bool publicKey(uint8_t pub[64]) = 0;
  // r then s, 32 bytes each, over a 32-byte digest the caller has computed.
  virtual bool sign(const uint8_t digest[32], uint8_t sig[64]) = 0;
};

}  // namespace ac
