<!-- The remediation brief, as pasted into the build session (30 Sep 2026) after the first hostile verification pass. Reproduced as received; formatting that the paste had flattened is left flattened. -->

AnnaChain — master remediation prompt

A hostile verification pass (docs/VERIFY.md) was run against this project. The firmware passed everything. The server failed claims C3, C4 and C9. This document is the brief for fixing that.

Paste everything below into a capable AI session with access to the project folder, or work through it by hand.

THE PROMPT

You are fixing verified defects in AnnaChain, a Smart India Hackathon 2026 entry for problem statement 26232 (farm-to-fork cold-chain traceability, Hardware category).

The project's entire value rests on one claim: a reading changed after it was recorded is detected. A verification pass proved the server does not actually do this. It stores parsed values, never re-checks a signature, and tells the buyer the record was "re-derived just now" when nothing was re-derived. That is not a rough edge — it is the product failing at the one thing it exists to do, while displaying a green tick.

Ground rules. These matter more than the fixes.

Fix the cause, not the symptom. Defect 1 is not "verify returns the wrong answer"; it is "the server threw away the evidence". Storing more derived values will not fix it.
Never weaken a claim to make a test pass. If a fix is hard, the right move is to do it or to remove the claim from the UI — never to soften the test. Deleting the buyer's "re-verify" button would be a legitimate fix. Leaving the button and lowering what it checks would not.
Every fix needs a failing test first. Write the test, watch it fail, then fix. There are currently zero tests for ~2,400 lines of Python, which is the root cause of every defect below.
Do not weaken what already works. The firmware passes 70 tests. They must still pass, unchanged in intent, when you are done.
Anything you cannot fix, write down in backend/README.md under the existing honest-limitations section, in plain words. An acknowledged limitation is respectable. A silent one is the defect you just fixed.

Work in the order given. Sections A–D are correctness and security and must all be done. E is important. F is polish. G is the root cause and is what stops this happening again.

A. The server throws away the evidence (defect 1 — critical, fixes C3)

What was proved. The verifier edited temp_c for record 200 directly in backend/annachain.db, changing 3.9 °C to 23.9 °C, then called GET /api/verify/639836161?full=true.

Result: ok: true, broken_at: null. The buyer's page showed a 23.9 °C peak with a green tick.

Why. backend/db.py stores the parsed fields plus digest, prev and sig as text. It never stores the original 84 record bytes. verify() in backend/app.py (~line 396) then compares stored prev text against stored digest text — two values an attacker with database access edits or leaves alone at will. No signature is ever checked after ingest. The dashboard's claim that verification "does not trust the database" is false as written.

The fix.

Add a raw BLOB NOT NULL column to the records table, with a migration in _migrate() for existing databases.
insert_record() stores the exact 84 bytes that arrived.
Treat raw as the only source of truth. The parsed columns are a denormalised index for charting and querying, nothing more.
Rewrite verify() so that for every record it:
recomputes digest = SHA256(raw[0:52]) and compares it to the stored digest;
re-checks the signature: HMAC(device_key, digest) == raw[52:84];
compares raw[20:52] (the record's own prev field) against the recomputed digest of the previous record, not the stored one;
re-parses raw and compares every parsed column — temp_c, rh_pct, c2h4_ppb, flags, batt_pct, ts — against the stored ones.
Any mismatch is tampering. Report which kind: bad_signature, chain_break, column_mismatch. A buyer deserves to know whether the reading was forged or the display was doctored.

Step 4's last bullet is the one that catches the exact attack that was demonstrated: the raw bytes were untouched, only the displayed column changed.

Prove it. backend/tests/test_tamper.py:

ingest a known-good trip, assert verify(full=True).ok is True
UPDATE records SET temp_c = 23.9 WHERE seq = 200 → assert ok is False, broken_at == 200, reason column_mismatch
flip one byte inside raw → assert bad_signature
change a stored digest → assert it is caught
assert /api/trace/... reports chain_ok: false for all three
B. An honest gap is reported to the buyer as fraud (defect 2 — critical, fixes C4)

What was proved. A node sent records 1–3, honestly declared 4–5 lost to flash overrun, then sent 6–10. The buyer's page said "The record has been altered."

This is the worst possible failure mode for this project. The node did exactly what the design promises — declared its loss instead of hiding it — and was called a liar for it.

Why. backend/app.py:414:

python
gap_after = {g["from_seq"] - 1 for g in db.gaps(device)}

For a gap of 4–5, the next record is seq 6, and the check at line 429 tests (r["seq"] - 1) not in gap_after, i.e. 5. The set contains from_seq - 1 = 3. It should contain to_seq = 5. An off-by-one, and the fleet trip has no gaps, so section 4 of the verification never walked this path.

The fix.

python
gap_after = {g["to_seq"] for g in db.gaps(device)}

Then check the surrounding logic honestly: the record following a declared gap legitimately has a prev that matches nothing, so it re-anchors the chain. Make sure it re-anchors rather than being skipped, and that the same handling exists in /api/trace, which computes the buyer's verdict separately.

Prove it. backend/tests/test_gap.py — build the 1–3, gap 4–5, 6–10 case exactly as the verifier did:

verify(full=True).ok is True
declared_gaps == [{from: 4, to: 5}]
the trace verdict is "Complete, with a declared hole", never "altered"
and a real break after a gap is still caught: edit record 8, assert failure
C. Anyone can re-key a device (defect 3 — critical, fixes the C6 hole)

What was proved. With no login at all, against a live consignment:

POST /api/register with an attacker's key → ok: true. db.py:203 overwrites the stored key.
POST /api/gap → ok: true. Unsigned, unauthenticated.
A record signed with the attacker's key → accepted.

The consignment now shows the buyer "The record has been altered" and a lost record, and every future record from the real node is refused as bad signature. A comment says registration is development-only; nothing enforces it.

The fix — three separate things.

/api/register requires an operator login. Enrolment is an operator action. Add Depends(operator).
A key can never be silently replaced. db.register_device() must insert a key only when the device is unknown. Replacing an existing key requires an explicit rotate=True and an admin role, and must be written to an audit table. A silent key swap is indistinguishable from an attack.
A gap notice must be proved by the device. It is a claim that data no longer exists, so it needs the same standing as a record. Add an authenticator computed by the node:
   mac = sign( SHA256( "ACGAP|" + device + "|" + from_seq + "|" + to_seq ) )

This is a change that crosses the whole stack, so change all of it:

ILink::declareGap() in lib/ac/ac_hal.h gains the MAC
Node::resync() in ac_node.cpp computes it with the existing signer
SerialLink wire format becomes G <device> <from> <to> <mac-hex>
SimLink, DumpLink in ac_sim.cpp, tools/dump.cpp, tools/fleet.cpp
backend/feed_sim.py, backend/bridge_serial.py
POST /api/gap verifies the MAC against the stored device key and refuses the notice if it does not match

Prove it. backend/tests/test_enrolment.py:

POST /api/register without a token → 401
registering a new device with a token → 200
re-registering an existing device with a different key → 409, key unchanged
the same with rotate=True and an admin → 200, and an audit row exists
POST /api/gap with a wrong MAC → 401 and no gap recorded
with the correct MAC → 200

Add a firmware test too: a gap notice produced by Node verifies against the device key.

D. The buyer's "re-verify" verifies nothing (defect 5 — critical, fixes C9)

What was proved. backend/static/trace.html:211 calls /api/verify/{device} without full=true. The server resumes from its own stored mark and checks zero records. The page then tells the buyer:

"All 420 readings verify. The chain was re-derived just now."

Both halves of that sentence are false. And after fixing A, it would still be the server checking itself — no public endpoint returns the records with their signatures, so an independent party cannot check anything.

The fix.

Call ?full=true. Non-negotiable: the buyer's button is the one place a cached result is worthless.
Add a public GET /api/records/{shipment_id} returning the raw record bytes as hex, paginated.
Verify the hash chain in the browser. Fetch the raw records and recompute the SHA-256 chain client-side with WebCrypto. Then the claim "re-derived just now" becomes true, and it is true independently of the server, which is what makes it worth saying.
Say precisely what was and was not checked. The chain is verified locally. The signature cannot be, because today it is HMAC and the key is symmetric — a buyer holding the key could forge records, so we do not hand it out. Write that on the page. When the ATECC608B lands and signatures become ECDSA, the public key can be published and the browser can check signatures too. That is a real reason the secure element is in the BOM, and the page should say so.

Prove it. backend/tests/test_public_verify.py:

/api/records/{shipment} needs no token and returns 84-byte records
the bytes match what was ingested
a Python re-implementation of the browser's chain walk agrees with /api/verify?full=true on a good trip and on a tampered one

Plus a Playwright check: click the button, assert the network call carries full=true, and assert the page reports failure on a tampered record.

E. The timestamp check cannot catch a wrong clock (defect 4)

What was proved. backend/checks.py:109 only asserts 1600000000 < ts < 2200000000 — anything from 2020 to 2039. It accepts timestamps ten years in the future, a year in the past, and time running backwards within one device. Section 8 of the verification leans on this check to catch a bad clock, and it cannot.

The fix. A record's time must be:

monotonic per device — records are sequential, so ts must be >=  the previous accepted record's ts. Track it in Verifier.
not ahead of the server by more than a small allowance (60 s covers clock skew; anything more is wrong).
not absurdly old relative to the device's first record.

Reject with a reason naming which rule failed.

Also, a real off-by-one-year. src/main.cpp:27:

cpp
static ArduinoClock clk(1758758400u);   // 25 Sep 2026; set properly from the server

1758758400 is 25 Sep 2025. 25 Sep 2026 is 1790294400. Fix the value, not the comment — a node shipping with a year-old clock is exactly what check 5 now exists to catch.

Prove it. backend/tests/test_time.py: backwards time rejected, far-future rejected, 30 s skew accepted, and a firmware assertion that the compiled base is the year the comment claims.

F. Smaller things, all real
#	What	Fix
F1	/api/ledger omits distributed until something is anchored	LocalLedger.verify() returns it on the empty path too
F2	Dashboard dropdown shows node C while the page shows node A on first load	Set shipSel.value from DEVICE once state arrives
F3	Simulated ethylene is displayed as measured. The real firmware correctly sends not fitted; the simulator invents ethylene and the dashboard shows a "ripening faster" alert from it. A judge watching the demo sees data from a sensor you do not have	Default tools/dump.cpp and tools/fleet.cpp to ethylene not fitted, matching real hardware, behind an explicit --ethylene flag. The demo should show what the device actually does
F4	/favicon.ico 404 on every page	Serve a small icon, or return 204
F5	Section 7 of the verification could not run: the deck is not in the project	Copy SIH2026_26232_AnnaChain_OfficialFormat.pptx and .pdf into docs/
F6	Test leftovers poison the demo: node C re-keyed, a fake gap, device 0D0D0001	Delete backend/annachain.db before any demo. Add it to make clean and say so in docs/DEMO.md

F3 deserves more than a table row. Showing invented sensor data next to real sensor data, unlabelled, is the same category of dishonesty as defect 1. Fix it before the demo, not after.

G. The root cause: no tests on the server (do this, or it all comes back)

2,409 lines of Python. Zero test files. Every defect above would have been caught by a test that takes a minute to write. The firmware has 70 tests and passed everything; that is not a coincidence.

Build backend/tests/ with pytest:

test_tamper.py, test_gap.py, test_enrolment.py, test_public_verify.py, test_time.py — the regressions above, each named for the defect it locks down
test_checks.py — all eight checks, each with a passing and a failing case
test_alerts.py — every alert rule fires when it should and stays quiet when it should not, including cross-node suspicion
test_shelflife.py — Q10 maths against hand-computed values; agreed range versus commodity ideal kept apart
test_epcis.py — commissioning survives adding a checkpoint (a real past bug)
test_auth.py — the whole boundary, protected and public

Add make backend-test, and make make test run both suites.

Target: every defect in this document has a test that fails before the fix and passes after.

Order of work
A and B — the product is not honest until these are done
C — anyone on the network can currently frame an honest consignment
D — stop the page claiming something it did not do
G for everything above, as you go
E, then F
Re-run docs/VERIFY.md end to end
Acceptance criteria
C1 through C9 in docs/VERIFY.md all PASS, including C3, C4 and C9
The exact attacks from the report are re-run and now fail: editing temp_c in the database is caught; the 1–3 / gap / 6–10 trip verifies clean; POST /api/register without a token is refused
70 firmware tests still pass, plus new ones for the gap MAC and the clock base
backend/tests/ exists and is green
Section 7 of the verification can run
Anything still unfixed is written down in backend/README.md in plain words
What would be a bad fix
Making verify() always return ok: false so tampering is "caught"
Storing a verified boolean column — an attacker with database access sets it to 1
Removing the buyer's re-verify button and leaving the text that promises it
Making the gap test pass by not declaring gaps
Deleting the ethylene chart instead of making the simulator honest
Marking any of this "done" without a test that failed first
