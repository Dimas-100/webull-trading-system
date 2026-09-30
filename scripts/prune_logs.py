"""Prune old rotated SDK logs from <repo>/logs/ and cap the append-forever suite logs.

The SDK's file logger (rerouted to <repo>/logs/ by webull_api/sdk_logging.py) appends
rotated *_sdk.log files that nothing in the app ever reads (see CLAUDE.md Gotchas).
Left alone they grow without bound — this repo hit ~46 MB / 76 files (one 33 MB file).

Two passes, both safe by construction (logs/ is gitignored and unread):
1. Delete files under logs/ whose mtime is older than --days (default 14). An
   actively-appended log (e.g. lab_cycle.log) has a recent mtime and is kept.
2. Trim the suite logs the scheduled bats append to forever (CAPPED_LOGS — nothing
   rotates them): above --cap-mb (default 5), keep only the last --keep-mb (default 1).

Pass --dry-run to preview both passes.

Usage:
    .venv/Scripts/python.exe scripts/prune_logs.py             # prune > 14 days + cap suite logs
    .venv/Scripts/python.exe scripts/prune_logs.py --days 30   # keep 30 days
    .venv/Scripts/python.exe scripts/prune_logs.py --dry-run   # preview only
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"

# Append-forever suite logs (each scheduled run appends with `>>`; nothing rotates them,
# and their fresh mtime means the age-based pass above never touches them).
CAPPED_LOGS = ("lab_cycle.log", "paper_suite.log", "sandbox_drill.log")


def prune(days: int, dry_run: bool) -> tuple[int, int]:
    """Delete files under logs/ older than `days`. Returns (removed_count, freed_bytes)."""
    if not LOGS_DIR.is_dir():
        print(f"No logs dir at {LOGS_DIR}")
        return (0, 0)
    cutoff = time.time() - days * 86400
    removed = 0
    freed = 0
    for path in sorted(LOGS_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if st.st_mtime >= cutoff:
            continue  # recent (or actively-appended) — keep
        action = "would remove" if dry_run else "removing"
        print(f"{action}: {path.name} ({st.st_size / 1e6:.1f} MB, "
              f"{(time.time() - st.st_mtime) / 86400:.0f}d old)")
        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                print(f"  ! failed: {e}", file=sys.stderr)
                continue
        removed += 1
        freed += st.st_size
    verb = "would free" if dry_run else "freed"
    print(f"\n{removed} file(s), {verb} {freed / 1e6:.1f} MB "
          f"(kept files newer than {days}d).")
    return (removed, freed)


def trim_capped(cap_bytes: int, keep_bytes: int, dry_run: bool) -> int:
    """Trim each CAPPED_LOGS file larger than cap_bytes down to its last keep_bytes (cut at
    a line boundary, dated marker line prepended; atomic tmp+replace). Safe whenever the
    scheduled jobs aren't mid-run: the bats hold no handle between runs. Returns the number
    of files trimmed."""
    keep_bytes = min(keep_bytes, cap_bytes)
    trimmed = 0
    for name in CAPPED_LOGS:
        path = LOGS_DIR / name
        try:
            size = path.stat().st_size
        except OSError:
            continue  # not created yet (e.g. sandbox_drill.log before the first drill)
        if size <= cap_bytes:
            continue
        action = "would trim" if dry_run else "trimming"
        print(f"{action}: {name} ({size / 1e6:.1f} MB > cap) -> last {keep_bytes / 1e6:.1f} MB")
        if not dry_run:
            data = path.read_bytes()[-keep_bytes:]
            nl = data.find(b"\n")
            if nl != -1:
                data = data[nl + 1:]  # start the kept tail on a whole line
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            marker = (f"==== trimmed {stamp}: kept last {len(data) / 1e6:.1f} MB "
                      f"of {size / 1e6:.1f} MB ====\n").encode()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_bytes(marker + data)
            tmp.replace(path)
        trimmed += 1
    return trimmed


def main() -> int:
    ap = argparse.ArgumentParser(description="Prune old unread logs from <repo>/logs/.")
    ap.add_argument("--days", type=int, default=14, help="Keep files newer than this (default 14).")
    ap.add_argument("--cap-mb", type=float, default=5.0,
                    help="Trim a suite log above this size (default 5).")
    ap.add_argument("--keep-mb", type=float, default=1.0,
                    help="Tail to keep when trimming (default 1).")
    ap.add_argument("--dry-run", action="store_true", help="Preview without deleting.")
    args = ap.parse_args()
    prune(args.days, args.dry_run)
    trim_capped(int(args.cap_mb * 2**20), int(args.keep_mb * 2**20), args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
