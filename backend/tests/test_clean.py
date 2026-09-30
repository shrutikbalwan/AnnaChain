"""`make clean` (tools/clean.py): removes the demo's leftovers, and refuses
loudly when it cannot.

The failure this guards against is real: a clean run from PowerShell with
-ErrorAction SilentlyContinue "succeeded" and deleted nothing, because uvicorn
held annachain.db open. The next demo ran on yesterday's test data, and nobody
knew until the server did not print its first-run credentials.
"""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CLEAN = ROOT / "tools" / "clean.py"

LEFTOVERS = ["demo", "selftest", "dump", "fleet", "fleet.exe", "selftest.exe",
             "fleet.capture", "demo.capture", "seed.capture", "records.jsonl",
             "backend/annachain.db", "backend/annachain.db-wal",
             "backend/annachain.db-shm", "backend/ledger.jsonl"]
KEEP = ["tools/seed/fleet.seed.capture", "backend/app.py", "notes.txt",
        "backend/tests/fleet.capture"]


def tree(tmp_path, names):
    for n in names:
        p = tmp_path / n
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def run(root):
    return subprocess.run([sys.executable, str(CLEAN), "--root", str(root)],
                          capture_output=True, text=True)


def test_removes_exactly_the_list(tmp_path):
    tree(tmp_path, LEFTOVERS + KEEP)
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    for n in LEFTOVERS:
        assert not (tmp_path / n).exists(), n
    for n in KEEP:                                # the committed seed capture above all
        assert (tmp_path / n).exists(), n


def test_nothing_to_remove_is_fine(tmp_path):
    r = run(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def _import_clean():
    sys.path.insert(0, str(CLEAN.parent))
    try:
        import clean                               # noqa: WPS433
        return clean
    finally:
        sys.path.pop(0)


def test_a_file_that_will_not_go_fails_loudly(tmp_path, monkeypatch, capsys):
    clean = _import_clean()
    tree(tmp_path, ["records.jsonl", "fleet.capture"])
    real = os.remove

    def stubborn(p):
        if Path(p).name == "records.jsonl":
            raise PermissionError(13, "Permission denied", str(p))
        real(p)

    monkeypatch.setattr(clean.os, "remove", stubborn)
    assert clean.main(["--root", str(tmp_path)]) != 0
    err = capsys.readouterr().err
    assert "records.jsonl" in err
    assert not (tmp_path / "fleet.capture").exists()   # the rest still went


def test_a_locked_database_names_the_server(tmp_path, monkeypatch, capsys):
    clean = _import_clean()
    tree(tmp_path, ["backend/annachain.db", "backend/ledger.jsonl"])

    def locked(p):
        if Path(p).name.startswith("annachain.db"):
            raise PermissionError(13, "The process cannot access the file because "
                                      "it is being used by another process", str(p))
        os.unlink(p)

    monkeypatch.setattr(clean.os, "remove", locked)
    assert clean.main(["--root", str(tmp_path)]) != 0
    err = capsys.readouterr().err
    assert "the server is still running" in err and "stop uvicorn first" in err
    assert (tmp_path / "backend/annachain.db").exists()


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to delete an open file")
def test_a_really_open_database_is_refused(tmp_path):
    """The exact situation: the server holds annachain.db through SQLite."""
    tree(tmp_path, ["fleet.capture", "backend/ledger.jsonl"])
    db = tmp_path / "backend" / "annachain.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    try:
        r = run(tmp_path)
    finally:
        conn.close()
    assert r.returncode != 0
    assert "annachain.db" in r.stderr
    assert "the server is still running" in r.stderr
    assert db.exists()
    assert not (tmp_path / "fleet.capture").exists()


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to delete an open file")
def test_the_report_reads_in_order_when_piped(tmp_path):
    """Through make the two streams share one pipe. The summary must come
    first, then what went wrong, then the verdict, not stdout's buffer last."""
    tree(tmp_path, ["fleet.capture", "backend/ledger.jsonl"])
    db = tmp_path / "backend" / "annachain.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    try:
        r = subprocess.run([sys.executable, str(CLEAN), "--root", str(tmp_path)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    finally:
        conn.close()
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert lines[0].startswith("clean: removed"), lines
    assert lines[-1].startswith("clean: FAILED"), lines


def test_the_makefile_uses_it():
    mk = (ROOT / "Makefile").read_text(encoding="utf-8")
    body = mk.split("\nclean:", 1)[1].split("\n.PHONY", 1)[0]
    assert "tools/clean.py" in body
    assert "rm " not in body
