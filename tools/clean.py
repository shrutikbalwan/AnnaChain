#!/usr/bin/env python3
"""`make clean`, portable, and loud when it cannot do its job.

    python tools/clean.py [--root DIR]

Removes what a demo run leaves behind: the built binaries (with or without
.exe), captures in the project root, records.jsonl, the server's database
(with its -wal and -shm files) and the local ledger. The committed seed
capture in tools/seed/ is not touched: only captures in the root are.

It exists because `rm` is not a Windows command, and because the obvious
PowerShell substitute (-ErrorAction SilentlyContinue) reports success while
deleting nothing when the server has the database open. A clean that silently
does nothing is how you demo on yesterday's test data. So every file that
cannot be removed is named, and the exit status is non-zero.
"""
import argparse
import glob
import os
import sys
from pathlib import Path

PATTERNS = [
    "demo", "selftest", "dump", "fleet", "*.exe",
    "*.capture", "records.jsonl",
    "backend/annachain.db*", "backend/ledger.jsonl",
]


def targets(root: Path):
    seen = []
    for pat in PATTERNS:
        for p in sorted(glob.glob(str(root / pat))):
            if os.path.isfile(p) and p not in seen:
                seen.append(p)
    return seen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]),
                    help="project root (default: the repository this script is in)")
    a = ap.parse_args(argv)
    root = Path(a.root)

    removed, failed = 0, []
    for p in targets(root):
        try:
            os.remove(p)
            removed += 1
        except OSError as e:
            failed.append((p, e))

    # Flushed: under make both streams share one pipe, and a buffered stdout
    # would print this summary after the errors instead of before them.
    print(f"clean: removed {removed} file(s)", flush=True)
    if not failed:
        return 0

    server_held = False
    for p, e in failed:
        rel = os.path.relpath(p, root)
        print(f"clean: COULD NOT REMOVE {rel}: {e.strerror or e}", file=sys.stderr)
        if Path(p).name.startswith("annachain.db") and isinstance(e, PermissionError):
            server_held = True
    if server_held:
        # On Windows an open file cannot be deleted, and the only thing that
        # holds annachain.db open is the server.
        print("clean: the server is still running — stop uvicorn first "
              "(Ctrl+C in its window), then run clean again.", file=sys.stderr)
    print(f"clean: FAILED: {len(failed)} file(s) left behind; the next run would "
          f"use stale data.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
