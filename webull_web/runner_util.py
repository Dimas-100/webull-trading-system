"""Shared plumbing for the Windows-scheduled paper runners (rsi2 / paper_eod / paper_eod_options /
options_entry): the best-effort file-lock, the same-day guard that each service hand-rolled
identically (differing only by the lock filename), and the 429 retry the broker-read call sites
share. This is the SHELL only — no strategy, market data, or placement logic lives here; each
runner keeps its own decision body. Under $ACTIVITY_DIR (the same store run_log uses), so the
lock and the run-log resolve to one place.
"""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path

from webull_api.paths import data_dir
from webull_api import run_log

_LOCK_STALE_SEC = 15 * 60


def _activity_dir() -> Path:
    return data_dir("activity", "ACTIVITY_DIR")


def _acquire(key: str, stamp: str) -> bool:
    d = _activity_dir()
    d.mkdir(parents=True, exist_ok=True)
    lock = d / f".{key}.lock"
    if lock.exists():
        try:
            if time.time() - lock.stat().st_mtime < _LOCK_STALE_SEC:
                return False
        except OSError:
            pass
    lock.write_text(f"{os.getpid()} {stamp}\n", encoding="utf-8")
    return True


def _release(key: str) -> None:
    try:
        (_activity_dir() / f".{key}.lock").unlink()
    except OSError:
        pass


@contextmanager
def filelock(key: str, stamp: str):
    """Best-effort single-runner lock at ``<activity>/.{key}.lock`` (reclaimed if its mtime is older
    than 15 min — a crashed run must not wedge the schedule forever). Yields True if THIS call
    acquired it (the caller should run), False if another run holds a fresh lock (the caller should
    no-op). Releases on exit only when this call acquired it — a blocked caller never deletes the
    lock the holder still owns. `key` matches the run-log key, so lock filenames are unchanged."""
    got = _acquire(key, stamp)
    try:
        yield got
    finally:
        if got:
            _release(key)


_RETRY_DELAYS = (10.0, 20.0, 40.0)  # spans a full minute — the quota window the suite exhausts


def retry_throttled(fn, *, delays=_RETRY_DELAYS, sleep=None):
    """Call fn(); on a Webull 429 (TOO_MANY_REQUESTS) wait and retry, once per delay. The suite's
    bar fetches routinely spend the per-minute API quota right before the real-book reads, so the
    tail runners ride out the throttle instead of recording 'unavailable' every night. Any other
    exception — and the 429 after the last delay — propagates unchanged."""
    sleep = sleep or time.sleep
    for delay in [*delays, None]:
        try:
            return fn()
        except Exception as e:
            if delay is None or "TOO_MANY_REQUESTS" not in str(e):
                raise
            sleep(delay)


def already_ran_today(key: str, today: str, force: bool) -> bool:
    """The same-day guard: True when the run-log's most recent entry for `key` is dated `today`
    (YYYY-MM-DD) and `force` is off. `today` is the ET calendar date from the service's now_et()."""
    if force:
        return False
    last = run_log.last_for(key)
    return bool(last and last.get("ts", "")[:10] == today)
