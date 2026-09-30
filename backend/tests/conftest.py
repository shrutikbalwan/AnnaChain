"""Every test gets its own database, its own ledger file and a fresh server state.

The server keeps some state in module globals (the verifier's chain tips, the
alert engine, silence tracking). Those are reset here, so no test can pass or
fail because of what an earlier one left behind.
"""
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend import app as app_mod, db, ledger  # noqa: E402
from backend.alerts import AlertEngine  # noqa: E402

from .helpers import login  # noqa: E402

KEY = b"annachain-test-key-node-a-000000"
DEV = 0x26232001


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "annachain.db")
    monkeypatch.setattr(db, "_local", threading.local())   # no stale connections
    monkeypatch.setattr(app_mod, "chain", ledger.LocalLedger(tmp_path / "ledger.jsonl"))
    app_mod.verifier.__init__()
    app_mod.engine.__init__(db)
    for d in (app_mod.last_ingest_at, app_mod.last_batch_n,
              app_mod.silence_start, app_mod.recovering):
        d.clear()
    app_mod._diagnosed.clear()
    app_mod.throttle.__init__()
    with TestClient(app_mod.app) as c:
        yield c


@pytest.fixture
def auth(client):
    return login(client)


@pytest.fixture
def engine():
    """An alert engine over a fake database that just remembers what it was told."""
    class FakeDB:
        def __init__(self):
            self.alerts = []

        def add_alert(self, dev, seq, ts, kind, severity, message):
            self.alerts.append({"device": dev, "seq": seq, "kind": kind,
                                "severity": severity, "message": message})

        # the suspect table (db.add_suspect / drop_suspect / suspects)
        def add_suspect(self, dev, truck, bucket, readings):
            self.suspects_ = getattr(self, "suspects_", {})
            self.suspects_[dev] = (truck, bucket, dict(readings))

        def drop_suspect(self, dev):
            getattr(self, "suspects_", {}).pop(dev, None)

        def suspects(self):
            return [{"device_id": d} for d in getattr(self, "suspects_", {})]

    fake = FakeDB()
    e = AlertEngine(fake)
    e.fake = fake
    return e
