"""Shared runner scaffold (webull_web/runner_util.py): the file-lock + same-day guard the four
paper runners hand-rolled identically. Characterizes the behavior BEFORE the services are refactored
onto it, so the extraction is provably equivalent."""
import os
import time

import pytest

from webull_api import run_log
from webull_web import runner_util


_THROTTLE_MSG = "HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many requests"


def _use_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))


def test_filelock_acquires_when_free_and_releases_on_exit(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    lock = tmp_path / ".k.lock"
    with runner_util.filelock("k", "stamp") as got:
        assert got is True
        assert lock.exists()
    assert not lock.exists()  # released on exit


def test_filelock_second_acquire_is_blocked_and_does_not_release(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    with runner_util.filelock("k", "s1") as got1:
        assert got1 is True
        with runner_util.filelock("k", "s2") as got2:
            assert got2 is False                     # a fresh lock is already held
        assert (tmp_path / ".k.lock").exists()       # the blocked ctx must NOT release the held lock
    assert not (tmp_path / ".k.lock").exists()


def test_filelock_reclaims_a_stale_lock(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    lock = tmp_path / ".k.lock"
    lock.write_text("999 old\n", encoding="utf-8")
    old = time.time() - 16 * 60                       # older than the 15-min stale window
    os.utime(lock, (old, old))
    with runner_util.filelock("k", "s") as got:
        assert got is True                            # reclaimed


def test_filelock_uses_the_key_as_the_lock_filename(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    with runner_util.filelock("options_entry", "s") as got:
        assert got is True
        assert (tmp_path / ".options_entry.lock").exists()  # matches the run-log key -> same filenames


def test_retry_throttled_retries_429_then_succeeds():
    calls, naps = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise Exception(_THROTTLE_MSG)
        return "ok"

    assert runner_util.retry_throttled(fn, sleep=naps.append) == "ok"
    assert len(calls) == 3
    assert naps == [10.0, 20.0]  # backoff grows between attempts


def test_retry_throttled_gives_up_after_the_final_attempt():
    calls = []

    def fn():
        calls.append(1)
        raise Exception(_THROTTLE_MSG)

    with pytest.raises(Exception, match="TOO_MANY_REQUESTS"):
        runner_util.retry_throttled(fn, sleep=lambda s: None)
    assert len(calls) == 4  # initial try + 3 retries, then the 429 propagates


def test_retry_throttled_other_errors_raise_immediately():
    calls, naps = [], []

    def fn():
        calls.append(1)
        raise ValueError("not a throttle")

    with pytest.raises(ValueError):
        runner_util.retry_throttled(fn, sleep=naps.append)
    assert len(calls) == 1 and naps == []  # no retry, no sleep


def test_already_ran_today(tmp_path, monkeypatch):
    _use_tmp(tmp_path, monkeypatch)
    assert runner_util.already_ran_today("rsi2", "2026-07-08", force=False) is False  # no run-log yet
    run_log.append([{"key": "rsi2", "ts": "2026-07-08T17:00:00-04:00", "result": "ok"}])
    assert runner_util.already_ran_today("rsi2", "2026-07-08", force=False) is True
    assert runner_util.already_ran_today("rsi2", "2026-07-08", force=True) is False   # force overrides
    assert runner_util.already_ran_today("rsi2", "2026-07-09", force=False) is False  # different day
    assert runner_util.already_ran_today("other", "2026-07-08", force=False) is False  # different key
