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
// Where a board's clock starts until the server sets it: the moment this file
// was compiled, not a date typed in by hand. A hand-typed date (it was 25 Sep
// 2026) gets staler every day the project sits, and 30 days after it every
// record from a board that had not reached a server was refused (check 5,
// MAX_HOLD_S). Built from the compiler's own clock, a freshly flashed board is
// hours out, not months.
//
// __DATE__ and __TIME__ are the build machine's LOCAL time, and they carry no
// zone. Local time is anywhere from UTC-12 to UTC+14, so kClockZoneMargin (14 h)
// is taken off: the base is then never AHEAD of true UTC at build time, and at
// most 26 h behind it (a UTC-12 build machine). Behind is the safe side: a clock
// that is ahead is refused by the server (MAX_SKEW_S), and records taken before
// the clock is set carry FLAG_TIMEUNSET, which is not held to MAX_HOLD_S.
//
// -DAC_CLOCK_BASE=<unix seconds> overrides it, for a build that knows UTC
// exactly (a CI job, or a reproducible build that must not embed its date).
//
// PlatformIO caches objects: a board flashed from a cache weeks after the
// object was compiled carries the COMPILE date. Records before first contact
// are flagged either way (FLAG_TIMEUNSET), so this costs accuracy of the
// flagged timestamps, never acceptance. selftest checks kClockBase against the
// clock of the machine it runs on.
namespace clockbase {
constexpr unsigned digit(char c) { return c >= '0' && c <= '9' ? (unsigned)(c - '0') : 0u; }
// "Mmm dd yyyy", as __DATE__ spells it (the day is space-padded).
constexpr unsigned month(const char* d) {
  return d[0] == 'J' ? (d[1] == 'a' ? 1u : d[2] == 'n' ? 6u : 7u)
       : d[0] == 'F' ? 2u
       : d[0] == 'M' ? (d[2] == 'r' ? 3u : 5u)
       : d[0] == 'A' ? (d[1] == 'p' ? 4u : 8u)
       : d[0] == 'S' ? 9u : d[0] == 'O' ? 10u : d[0] == 'N' ? 11u : 12u;
}
constexpr unsigned day(const char* d) { return (d[4] == ' ' ? 0u : digit(d[4])) * 10u + digit(d[5]); }
constexpr unsigned year(const char* d) {
  return digit(d[7]) * 1000u + digit(d[8]) * 100u + digit(d[9]) * 10u + digit(d[10]);
}
// Days since 1970-01-01 (Howard Hinnant's days_from_civil, for years >= 1970).
constexpr uint32_t doe(uint32_t yoe, unsigned m, unsigned d) {
  return yoe * 365u + yoe / 4u - yoe / 100u + (153u * (m > 2 ? m - 3u : m + 9u) + 2u) / 5u + d - 1u;
}
constexpr uint32_t days2(uint32_t y, unsigned m, unsigned d) {
  return (y / 400u) * 146097u + doe(y % 400u, m, d) - 719468u;
}
constexpr uint32_t days(uint32_t y, unsigned m, unsigned d) { return days2(y - (m <= 2 ? 1u : 0u), m, d); }
// "hh:mm:ss", as __TIME__ spells it.
constexpr uint32_t secs(const char* t) {
  return (digit(t[0]) * 10u + digit(t[1])) * 3600u + (digit(t[3]) * 10u + digit(t[4])) * 60u +
         digit(t[6]) * 10u + digit(t[7]);
}
}  // namespace clockbase

// __DATE__/__TIME__ strings to unix seconds, reading them as if they were UTC.
constexpr uint32_t buildTime(const char* date, const char* time) {
  return clockbase::days(clockbase::year(date), clockbase::month(date), clockbase::day(date)) * 86400u +
         clockbase::secs(time);
}

constexpr uint32_t kClockZoneMargin = 14u * 3600u;   // the furthest local time is ahead of UTC
#ifdef AC_CLOCK_BASE
constexpr uint32_t kClockBase = (uint32_t)(AC_CLOCK_BASE);
#else
constexpr uint32_t kClockBase = buildTime(__DATE__, __TIME__) - kClockZoneMargin;
#endif

struct IClock {
  virtual ~IClock() {}
  virtual uint32_t now() = 0;            // unix seconds
  virtual void     sleep(uint32_t ms) = 0;
  // Has an authority (the server, NTP) told this clock the wall-clock time
  // since it powered up? A clock that has not is uptime counted from
  // kClockBase, and the records it stamps carry FLAG_TIMEUNSET.
  virtual bool     isSet() const { return true; }
  // The authority's time: from now on now() counts from it.
  virtual void     set(uint32_t unixNow) { (void)unixNow; }
  // Never read earlier than t from here on. Not a claim about wall-clock time;
  // this is how a node that rebooted resumes after its last timestamp.
  virtual void     atLeast(uint32_t t) { (void)t; }
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
  bool     simulated = false;            // a value here was invented (FLAG_SIMULATED)
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
  // The server's clock (unix seconds), as it came with the answer to the last
  // queryLastAck() that returned true. False if that answer carried no time:
  // an older server, bridge or gateway, or one that does not know the time.
  // The node sets its clock from it (Node::resync). It is not signed: whatever
  // carries it can shift it, within what the server's check 5 lets through
  // (docs/CRYPTO.md).
  virtual bool serverTime(uint32_t& unixNow) { (void)unixNow; return false; }
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
// written now and exercised on arrival day without pretending otherwise.
//
// Nothing signs records with it yet, by decision (docs/CRYPTO.md): "driver
// now, format later". Moving records onto it is a planned, separate change — a
// v2 record of 117 bytes (version byte, v1 body, 64-byte signature), verified
// by the server and by the buyer's browser against a published public key —
// specified in docs/CRYPTO.md. docs/HIL.md step 4 proves the chip itself.
struct IEcdsaSigner {
  virtual ~IEcdsaSigner() {}
  virtual bool begin() = 0;
  // The device's public key, X then Y, 32 bytes each.
  virtual bool publicKey(uint8_t pub[64]) = 0;
  // r then s, 32 bytes each, over a 32-byte digest the caller has computed.
  virtual bool sign(const uint8_t digest[32], uint8_t sig[64]) = 0;
};

}  // namespace ac
