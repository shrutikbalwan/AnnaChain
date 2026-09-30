"""The buyer's page, in a real browser.

Clicks "Re-check every reading now" and checks what actually happens: the
server is asked for the full walk, the signed records are fetched and walked on
the page, and a doctored reading is reported as a failure. Also checks that the
page's own SHA-256 (used where WebCrypto is missing) agrees with WebCrypto.

Needs Playwright and a local Chrome or Chromium; skipped otherwise.
"""
import socket
import threading
import time

import pytest

from backend import app as app_mod, db

from .conftest import DEV, KEY
from .helpers import enrol, ingest, trip

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

SHIP = f"AC-{DEV:08X}"


def _browser(p):
    for kw in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
        try:
            return p.chromium.launch(headless=True, **kw)
        except Exception:
            continue
    pytest.skip("no Chrome, Edge or Playwright Chromium available")


@pytest.fixture
def live(client, auth):
    enrol(client, auth, DEV, KEY)
    assert ingest(client, trip(KEY, DEV, 60)) == 60
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app_mod.app, host="127.0.0.1", port=port,
                                           log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    t.join(5)


def recheck(live):
    with playwright.sync_playwright() as p:
        br = _browser(p)
        page = br.new_page()
        errors, verify_urls = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("request", lambda r: verify_urls.append(r.url) if "/api/verify/" in r.url else None)
        page.goto(f"{live}/t/{SHIP}")
        page.wait_for_selector("#btn")
        page.click("#btn")
        page.wait_for_function("document.querySelector('#btn').disabled === false"
                               " && !document.querySelector('#vout').textContent.includes('fingerprint of the one before')")
        text = page.inner_text("#vout")
        same = page.evaluate("""async () => {
            const a = new Uint8Array(1000); for (let i = 0; i < a.length; i++) a[i] = (i * 31) & 255;
            for (const n of [0, 3, 52, 55, 56, 64, 119, 1000]) {
              const x = await crypto.subtle.digest('SHA-256', a.subarray(0, n));
              const y = sha256js(a.subarray(0, n));
              if (!same(new Uint8Array(x), y)) return n;
            }
            return true; }""")
        br.close()
    return text, verify_urls, errors, same


def test_good_trip_rechecks_here_and_asks_for_the_full_walk(live):
    text, urls, errors, same = recheck(live)
    assert errors == []
    assert urls and all("full=true" in u for u in urls)
    assert "Checked here: all 60 readings chain together" in text
    assert "Checked by our server: all 60 signatures hold" in text
    assert "ATECC608B" in text
    assert same is True, f"sha256js disagrees with WebCrypto at length {same}"


def test_doctored_reading_is_reported_on_the_page(live):
    c = db.conn()
    c.execute("UPDATE records SET temp_c = 23.9 WHERE device_id=? AND seq = 30", (DEV,))
    c.commit()
    text, urls, errors, _ = recheck(live)
    assert errors == []
    assert "Checked here: reading 30 fails" in text
    assert "23.9" in text
    assert "Checked by our server: reading 30 fails" in text
