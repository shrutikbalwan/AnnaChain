# Signatures: what ships, and why

## The decision

**Records are signed with HMAC-SHA256 and carry a 32-byte signature (format
v1). ECDSA on the ATECC608B is a driver today and a separate, planned record
format later.**

Decided by the team on 30 September 2026, in their words: *"Driver now, format
later."* That is option (a) of the build brief: ship HMAC, specify the
migration, do not half-change the format in the week the parts arrive.

## Why

- **The secure element is on the BOM and the interface exists.**
  `lib/ac/ac_atecc.*` (`AteccSigner`) drives an ATECC608B-TNGTLS through
  `IEcdsaSigner` (`lib/ac/ac_hal.h`): public key out, 64-byte P-256 signature
  over a digest the caller supplies, key generated in the chip and never
  readable. `docs/HIL.md` step 4 is how it is proved on the bench. It is not
  wired into records, on purpose.
- **An ECDSA signature does not fit a v1 record.** `ac_record.h` gives the
  signature 32 bytes; P-256 needs 64. Putting the chip into the record path is
  a record format change — node, gateway, LoRa frames, flash ring, server,
  captures, the buyer's page — not a driver swap.
- **Symmetric signing is a scope decision, not an oversight.** The v1 record,
  its chain and the eight checks are what the whole project is tested on (the
  firmware selftest, the backend suite). Changing the record format in the same
  week the radio, the NFC reader and the secure element are first powered would
  put every one of those at risk at once.

## What it costs, said plainly

- **A buyer cannot verify a signature — only the hash chain.** The trace page
  re-derives every record's SHA-256 and every link on the phone, and checks that
  the values shown are the values signed. The signature itself can only be
  checked by our server, because checking an HMAC needs the key, and handing out
  the key would let anyone forge records. For that one step the buyer trusts us.
- **Whoever holds the key and the database can rewrite history.** That is us.
  With both, every record could be re-signed and re-chained and every check
  would pass. Only an anchor we do not control would catch it, and the local
  ledger is not one.
- With ECDSA the server holds only public keys, and both of those go away.

## How a format is identified — v1 and every later version

A v1 record is **exactly 84 bytes, and has no version byte**. Its layout is in
`lib/ac/ac_record.h`. Its bytes are the evidence — `checks.verify_chain`
re-derives everything from them — and every node, capture and database in
existence holds them without a version byte. Adding a byte to v1 would change
every one of those bytes, so v1 is frozen as it is.

The rule, in both `lib/ac/ac_record.*` (`recordFormat`) and
`backend/checks.py` (`record_format`):

1. **84 bytes long → v1.** Always, whatever its first byte is. (Byte 0 of a v1
   record is the low byte of the device id. Node B, `0x26232002`, starts with
   `0x02`; its records are v1.)
2. **Any other length → byte 0 is the format version**, and it is 2 or more.
   Each version has one fixed length, and **no version may ever be 84 bytes
   long.** The version byte is the first byte of the signed body, so it cannot
   be changed without breaking the signature.
3. **Anything else is refused by name**: the server answers `not a record in
   any format this server knows: <n> bytes, record format version <v> by its
   first byte …`, and nothing is stored. An 85-byte "v1 with a version byte" is
   not a format.

Today the only known format is v1. `backend/tests/test_record_format.py` and
the selftest section *"The v1 record format is frozen, and has no version
byte"* hold the same v1 record as bytes, check that it still verifies
unchanged, and check that a v2-shaped record is refused rather than mistaken for
v1.

The containers that carry no length are fixed-size and hold one format: the
node's flash ring (`kRecBytes` slots, one format per device), LoRa frames and
gateway buffer slots (`kRecBytes`, typed by the frame kind byte beside them).
A later format needs its own frame kind and its own slot size; it does not
share v1's.

## The migration, specified (not built)

Format **v2**, ECDSA P-256:

| Bytes | Field |
|---|---|
| 0 | version = `0x02` |
| 1–52 | the v1 body, unchanged: device, seq, ts, temp, rh, c2h4, flags, batt, prev |
| 53–116 | signature r ‖ s, 64 bytes, over SHA-256(bytes 0–52) |

117 bytes. Its digest (what the next record's `prev` carries) is SHA-256 of
bytes 0–52, version byte included. The first v2 record after a node's last v1
record carries the v1 digest (SHA-256 of that record's 52-byte body) as its
`prev`, so one device's chain crosses the change without a break.

What has to change, and nothing here has been changed:

- **Node** (`ac_node.cpp`, `ac_hal.h`): sign through `IEcdsaSigner`; build and
  store 117-byte records; a separate flash file for the v2 ring.
- **Enrolment** (`/api/register`, `device_keys`): register the chip's 64-byte
  public key with a key type; a device moves from HMAC to ECDSA at a stated
  sequence number, audited like a key rotation.
- **Server** (`checks.py`): `FORMATS[2] = 117`; per-record dispatch on
  `record_format`; ECDSA verification (the `cryptography` package) in check 2
  and in `verify_chain`; `raw` already stores its length.
- **Gap notices**: signed by the same ECDSA key; the notice frame grows.
- **LoRa** (`ac_lora.*`): a v2 record frame is 2 + 117 = 119 bytes. By the
  Semtech formula at SF9/125 kHz/CR 4/7 that is about **0.86 s** on air
  against about 0.66 s for v1 — **about 31 % more airtime per record**, before
  any retry. Recheck the duty cycle against the limits you work to.
- **Gateway** (`ac_gateway.*`): a new frame kind, and buffer slots sized for
  the larger frame (fewer frames in the same RAM).
- **Buyer's page** (`trace.html`): parse by length/version and verify ECDSA
  with WebCrypto against the published public key — the step that finally lets
  a buyer check a signature without trusting us.
- **Captures and tools** (`dump.cpp`, `fleet.cpp`, `feed_sim.py`): new record
  length; the `K` line carries a public key.
