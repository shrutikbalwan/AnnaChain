"""AnnaChain — anchoring a batch of records to a ledger.

This file exists to stop one specific lie.

The deck says "Hyperledger Fabric". We do not have a Fabric network, and
pretending a local table is one would be exactly the dishonesty the whole
project is built to prevent. So the ledger is an interface with two
implementations:

  * `LocalLedger` — real, working, and running today. An append-only,
    hash-linked log. Every entry carries the hash of the one before it, so the
    ledger itself has the property the records have: you cannot change an old
    entry without invalidating every entry after it. It is not distributed, and
    it does not claim to be.

  * `FabricLedger` — the interface Fabric will slot into, with the chaincode
    signature written down and `available()` returning False until it exists.

Every anchor records which backend wrote it. `/api/anchor` returns
`"distributed": false` for a local anchor, in as many words. When a judge asks
"is this actually on a blockchain?", the honest answer is already in the API
response, and that is a much better place for it to be than in a footnote.

What a local ledger does and does not give you:

  ✓ tamper-evidence — altering an old entry breaks every later one
  ✓ an auditable order of events
  ✗ independence — we host it, so we could in principle rewrite the whole chain
  ✗ multi-party consensus — nobody else validates what we write

That last pair is exactly what Fabric adds, and it is why it is in the design.
"""
import hashlib, json, time
from pathlib import Path


class Ledger:
    name = "abstract"
    distributed = False

    def available(self) -> bool:
        raise NotImplementedError

    def append(self, entry: dict) -> dict:
        raise NotImplementedError

    def verify(self) -> dict:
        raise NotImplementedError


class LocalLedger(Ledger):
    """Append-only and hash-linked. Real, and honest about its limits."""

    name = "local"
    distributed = False

    def __init__(self, path: Path = None):
        self.path = path or Path(__file__).with_name("ledger.jsonl")

    def available(self) -> bool:
        return True

    def _tip(self) -> str:
        last = None
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        last = line
        return json.loads(last)["this"] if last else "0" * 64

    def append(self, entry: dict) -> dict:
        prev = self._tip()
        body = dict(entry)
        body["prev"] = prev
        body["at"] = time.time()
        # The hash covers the entry and the previous hash, so the ledger is a
        # chain in the same sense the records are.
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        body["this"] = hashlib.sha256(canonical.encode()).hexdigest()
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(body) + "\n")
        return {"txid": body["this"], "backend": self.name,
                "distributed": self.distributed}

    def verify(self) -> dict:
        if not self.path.exists():
            return {"entries": 0, "ok": True, "broken_at": None, "tip": "0" * 64,
                    "backend": self.name, "distributed": self.distributed}
        prev, n, broken = "0" * 64, 0, None
        with open(self.path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if not line.strip():
                    continue
                e = json.loads(line)
                this = e.pop("this")
                if e.get("prev") != prev:
                    broken = i
                    break
                canonical = json.dumps(e, sort_keys=True, separators=(",", ":"))
                if hashlib.sha256(canonical.encode()).hexdigest() != this:
                    broken = i
                    break
                prev, n = this, n + 1
        return {"entries": n, "ok": broken is None, "broken_at": broken,
                "tip": prev, "backend": self.name,
                "distributed": self.distributed}


class FabricLedger(Ledger):
    """Hyperledger Fabric, via MeitY Vishvasya. Not wired up.

    When it is, this is the shape of it — kept here so the work is scoped and
    nobody has to guess later:

        from hfc.fabric import Client                 # fabric-sdk-py
        cli = Client(net_profile="network.json")
        user = cli.get_user("org1.example.com", "Admin")
        await cli.chaincode_invoke(
            requestor=user, channel_name="annachain",
            peers=["peer0.org1.example.com"], cc_name="anchors",
            fcn="AnchorBatch",
            args=[device_id, str(from_seq), str(to_seq), merkle_root],
        )

    The chaincode stores (device, from_seq, to_seq, root, timestamp) and
    refuses to overwrite an existing key, so an anchor cannot be replaced once
    written. The value that goes on-chain is the same Merkle root the local
    ledger already stores — moving to Fabric changes who guarantees it, not
    what is guaranteed.
    """

    name = "hyperledger-fabric"
    distributed = True

    def available(self) -> bool:
        return False

    def append(self, entry: dict) -> dict:
        raise RuntimeError(
            "Fabric network is not configured. Anchors are being written to the "
            "local hash-linked ledger instead, and are reported as such."
        )

    def verify(self) -> dict:
        return {"entries": 0, "ok": True, "backend": self.name,
                "distributed": True, "note": "not configured"}


def get_ledger(prefer_distributed: bool = True) -> Ledger:
    """Use Fabric when it is really there; otherwise the local ledger, and say so."""
    if prefer_distributed:
        fab = FabricLedger()
        if fab.available():
            return fab
    return LocalLedger()
