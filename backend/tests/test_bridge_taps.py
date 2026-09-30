"""NFC custody taps reach the server over the USB-tethered configuration.

The node prints each PN532 tap on its serial line as
    T <device> <assign|tap> <uid-hex> <unix-time>
(src/main.cpp, env:node_lora). ac_nfc.h said backend/bridge_serial.py turns
that into a checkpoint; it did not handle T lines at all, so a tap reached no
one. Over LoRa a tap is not carried (there is no FRAME_TAP): handover is the
USB-tethered configuration, and these tests are what makes that sentence true.

The serial port is faked: it yields the lines a node would print, then closes.
"""
import io
import urllib.error

import pytest

from backend import bridge_serial, db

from .conftest import DEV, KEY

serial = pytest.importorskip("serial")      # pyserial, in requirements.txt
SHIP = f"AC-{DEV:08X}"


class FakePort:
    def __init__(self, lines):
        self.lines = [l.encode() + b"\n" for l in lines]
        self.written = []

    def readline(self):
        if not self.lines:
            raise serial.SerialException("port closed")
        return self.lines.pop(0)

    def write(self, b):
        self.written.append(b.decode())


@pytest.fixture
def bridge(client, monkeypatch):
    def call(base, path, payload=None, method="POST", token=None):
        h = {"Authorization": "Bearer " + token} if token else {}
        r = (client.post(path, json=payload, headers=h) if method == "POST"
             else client.get(path, headers=h))
        if r.status_code >= 400:
            raise urllib.error.HTTPError(path, r.status_code, r.text, {},
                                         io.BytesIO(r.content))
        return r.json()

    monkeypatch.setattr(bridge_serial, "call", call)

    def run(lines):
        port = FakePort(lines)
        monkeypatch.setattr(serial, "Serial", lambda *a, **k: port)
        with pytest.raises(SystemExit):              # the port closing ends it
            bridge_serial.main(["COM9"])
        return port
    return run


def test_taps_become_checkpoints(client, bridge):
    t0 = 1790400000
    bridge([f"K {DEV} {KEY.hex()}",
            f"T {DEV} assign 04a1b2c3d4e5f6 {t0}",
            f"T {DEV} tap 04a1b2c3d4e5f6 {t0 + 3600}"])
    cps = db.checkpoints(SHIP)
    assert [c["biz_step"] for c in cps] == ["commissioning", "inspecting"]
    assert [c["ts"] for c in cps] == [t0, t0 + 3600]
    assert all("04a1b2c3d4e5f6" in c["place"] for c in cps)
    # and it says what a tap is worth: logged, not signed, not in the chain
    assert all("not signed" in c["note"] for c in cps)


def test_a_tap_from_a_device_with_no_shipment_is_reported_not_fatal(client, bridge, capsys):
    bridge([f"T {DEV + 7} tap 04a1b2c3 1790400000"])
    assert "tap" in capsys.readouterr().out.lower()
    assert db.checkpoints(f"AC-{DEV + 7:08X}") == []
