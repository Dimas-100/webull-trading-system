"""scripts/prune_logs.py — age-based prune + suite-log size cap (pure filesystem, tmp dirs)."""
from __future__ import annotations

import os
import time

import pytest

from scripts import prune_logs


@pytest.fixture()
def logs_dir(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    monkeypatch.setattr(prune_logs, "LOGS_DIR", d)
    return d


def test_prune_removes_old_keeps_recent(logs_dir):
    old = logs_dir / "webull_data_sdk.log.2026-01-01_09"
    old.write_text("stale")
    stale_mtime = time.time() - 30 * 86400
    os.utime(old, (stale_mtime, stale_mtime))
    fresh = logs_dir / "lab_cycle.log"
    fresh.write_text("recent")

    removed, freed = prune_logs.prune(days=14, dry_run=False)

    assert removed == 1 and freed == len("stale")
    assert not old.exists() and fresh.exists()


def test_prune_dry_run_deletes_nothing(logs_dir):
    old = logs_dir / "webull_trade_sdk.log.2026-01-01_09"
    old.write_text("stale")
    stale_mtime = time.time() - 30 * 86400
    os.utime(old, (stale_mtime, stale_mtime))

    removed, _ = prune_logs.prune(days=14, dry_run=True)

    assert removed == 1 and old.exists()


def test_trim_caps_oversized_suite_log_at_a_line_boundary(logs_dir):
    log = logs_dir / "paper_suite.log"
    lines = [f"run {i:04d}: all runners green\n" for i in range(200)]
    log.write_text("".join(lines))
    orig_size = log.stat().st_size

    trimmed = prune_logs.trim_capped(cap_bytes=1000, keep_bytes=300, dry_run=False)

    assert trimmed == 1
    content = log.read_text()
    assert content.startswith("==== trimmed ")
    body = content.split("\n", 1)[1]
    assert body == "".join(lines)[-300:].split("\n", 1)[1]  # tail, cut at a whole line
    assert log.stat().st_size < orig_size


def test_trim_skips_small_missing_and_unlisted_logs(logs_dir):
    small = logs_dir / "lab_cycle.log"
    small.write_text("tiny\n")
    unlisted = logs_dir / "webull_data_sdk.log"  # live SDK log: rotates itself, never trimmed
    unlisted.write_text("x" * 5000)

    trimmed = prune_logs.trim_capped(cap_bytes=1000, keep_bytes=300, dry_run=False)

    assert trimmed == 0
    assert small.read_text() == "tiny\n"
    assert unlisted.stat().st_size == 5000


def test_trim_dry_run_modifies_nothing(logs_dir):
    log = logs_dir / "lab_cycle.log"
    log.write_text("x" * 5000)

    trimmed = prune_logs.trim_capped(cap_bytes=1000, keep_bytes=300, dry_run=True)

    assert trimmed == 1
    assert log.read_text() == "x" * 5000
