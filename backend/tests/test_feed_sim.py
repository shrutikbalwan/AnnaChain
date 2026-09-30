"""feed_sim.py refuses a capture the server would refuse, before posting anything.

A capture's trip ends at the moment it was made, and the server refuses a
reading older than checks.MAX_HOLD_S. Replaying a month-old capture used to post
every record, have every one refused, and leave nothing on screen saying why.
The window comes from the server (/api/state), not from a second constant here.

feed_sim talks HTTP through its own post() and get(); these tests route both to
the in-process test server, and record every POST it makes.
"""
import io
import time
import urllib.error

import pytest

from backend import checks, feed_sim

from .conftest import DEV, KEY
from .helpers import record, digest

DAY = 86400
WINDOW = checks.MAX_HOLD_S


def capture(tmp_path, first_age_s, n=10):
    """n chained records of one node, the first first_age_s old, the last now."""
    now = int(time.time()) - 60
    start = now - first_age_s
    step = max(1, first_age_s // max(1, n - 1))
    lines, prev = [f"K {DEV} {KEY.hex()}"], b"\0" * 32
    for seq in range(1, n + 1):
        r = record(KEY, DEV, seq, min(now, start + (seq - 1) * step), prev=prev)
        lines.append("R " + r.hex())
        prev = digest(r)
    lines.append("# done")
    p = tmp_path / "trip.capture"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


@pytest.fixture
def run(client, monkeypatch):
    posts = []

    def http(method, path, payload=None, token=None):
        h = {"Authorization": "Bearer " + token} if token else {}
        r = (client.post(path, json=payload, headers=h) if method == "POST"
             else client.get(path, headers=h))
        if r.status_code >= 400:
            raise urllib.error.HTTPError(path, r.status_code, r.text, {},
                                         io.BytesIO(r.content))
        return r.json()

    def fake_post(base, path, payload, token=None):
        posts.append(path)
        return http("POST", path, payload, token)

    def fake_get(base, path, token=None):
        return http("GET", path, None, token)

    monkeypatch.setattr(feed_sim, "post", fake_post)
    monkeypatch.setattr(feed_sim, "get", fake_get, raising=False)
    monkeypatch.setattr(feed_sim.time, "sleep", lambda s: None)

    def go(*argv):
        posts.clear()
        feed_sim.main(list(argv))
        return posts
    go.posts = posts
    return go


def stored(client):
    from backend import db
    return db.record_count()


def test_the_window_comes_from_the_server(client, auth):
    s = client.get("/api/state", headers=auth).json()
    assert s["max_hold_s"] == checks.MAX_HOLD_S


def test_a_fresh_capture_feeds(client, run, tmp_path, capsys):
    run(capture(tmp_path, 3600), "--rate", "1000")
    assert stored(client) == 10
    assert "stale" not in capsys.readouterr().err.lower()


def test_a_stale_capture_exits_non_zero_and_posts_nothing(client, run, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        run(capture(tmp_path, WINDOW + 2 * DAY), "--reset")
    assert e.value.code not in (0, None)
    # signing in is the only POST: it is how /api/state can be read at all
    assert run.posts == ["/api/login"]
    assert stored(client) == 0
    err = capsys.readouterr().err
    assert "regenerate: mingw32-make fleet" in err
    assert "feed_sim.py" in err            # and the command to replay it with


def test_allow_stale_feeds_anyway_with_a_warning(client, run, tmp_path, capsys):
    run(capture(tmp_path, WINDOW + 2 * DAY), "--allow-stale", "--rate", "1000")
    assert "/api/register" in run.posts and "/api/ingest" in run.posts
    warn = [l for l in capsys.readouterr().err.splitlines() if "--allow-stale" in l]
    assert len(warn) == 1
    # the server still applies check 5: an archived capture is refused there
    assert stored(client) == 0


def test_thirty_percent_of_the_window_feeds_with_a_warning(client, run, tmp_path, capsys):
    run(capture(tmp_path, int(WINDOW * 0.30)), "--rate", "1000")
    assert stored(client) == 10
    err = capsys.readouterr().err
    assert "days old" in err and "regenerate" in err
