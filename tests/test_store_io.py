"""Tests for the shared atomic file-store I/O helper (webull_web.store_io).

Why this exists: every JSON/JSONL store used to write with Path.write_text, which
truncates the target before writing. A crash/power-loss mid-write left a corrupt
file that the next load() could not parse — losing the paper account, strategies,
intents, or practice sessions. atomic_write_* writes to a temp file and os.replace()s
it into place, so a reader always sees either the old or the new content.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from webull_web import journal_store, paper_store, store_io


def test_atomic_write_text_roundtrips(tmp_path):
    p = tmp_path / "a.txt"
    store_io.atomic_write_text(p, "hello")
    assert p.read_text(encoding="utf-8") == "hello"


def test_atomic_write_json_roundtrips(tmp_path):
    p = tmp_path / "a.json"
    store_io.atomic_write_json(p, {"x": 1, "y": [1, 2, 3]})
    assert json.loads(p.read_text(encoding="utf-8")) == {"x": 1, "y": [1, 2, 3]}


def test_atomic_write_overwrites_existing(tmp_path):
    p = tmp_path / "a.json"
    store_io.atomic_write_json(p, {"v": 1})
    store_io.atomic_write_json(p, {"v": 2})
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": 2}


def test_creates_parent_dirs(tmp_path):
    p = tmp_path / "nested" / "deep" / "a.json"
    store_io.atomic_write_json(p, {"ok": True})
    assert json.loads(p.read_text(encoding="utf-8")) == {"ok": True}


def test_no_temp_file_left_after_success(tmp_path):
    p = tmp_path / "a.json"
    store_io.atomic_write_json(p, {"v": 1})
    # The directory must contain only the target — no orphaned ".tmp" files, and a
    # glob('*.json') (used by the list_* store helpers) must see exactly the target.
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*.json")) == [p]


def test_failed_write_preserves_original_and_leaves_no_temp(tmp_path):
    p = tmp_path / "a.json"
    store_io.atomic_write_json(p, {"v": "original"})
    # A non-serializable payload makes json.dumps raise AFTER the original exists.
    with pytest.raises(TypeError):
        store_io.atomic_write_json(p, {"bad": object()})
    # The original is untouched and no half-written temp file is left behind.
    assert json.loads(p.read_text(encoding="utf-8")) == {"v": "original"}
    assert list(tmp_path.glob("*.tmp")) == []
    assert list(tmp_path.glob("*")) == [p]


def test_journal_load_lines_skips_corrupt_lines(tmp_path, monkeypatch):
    """A partial/corrupt line (e.g. a crash mid-append) must not break the whole read."""
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    f = tmp_path / "fills.jsonl"
    f.write_text(
        json.dumps({"id": "1", "ok": True}) + "\n"
        + "{this is a half-written line\n"
        + json.dumps({"id": "2", "ok": True}) + "\n",
        encoding="utf-8",
    )
    rows = journal_store._load_lines("fills.jsonl")
    assert [r["id"] for r in rows] == ["1", "2"]


def test_paper_load_quarantines_corrupt_and_returns_none(tmp_path, monkeypatch):
    """A corrupt paper account must not 500 forever — quarantine it and start fresh."""
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    (tmp_path / "default.json").write_text("{not valid json", encoding="utf-8")
    assert paper_store.load() is None
    assert (tmp_path / "default.json.corrupt").exists()  # data preserved, not silently lost
    assert not (tmp_path / "default.json").exists()


def test_journal_read_meta_tolerates_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    (tmp_path / "meta.json").write_text("garbage", encoding="utf-8")
    assert journal_store.read_meta() == {}


def test_dedup_by_id_keeps_first_preserves_order():
    rows = [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "a", "v": 3}]
    assert journal_store._dedup_by_id(rows) == [{"id": "a", "v": 1}, {"id": "b", "v": 2}]


def test_journal_append_idempotent_under_concurrency(tmp_path, monkeypatch):
    """The append lock + on-write dedup must prevent a double-append race writing dup lines."""
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    records = [{"id": str(i), "v": i} for i in range(5)]
    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(lambda _: journal_store._append("fills.jsonl", records), range(8)))
    lines = [ln for ln in (tmp_path / "fills.jsonl").read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 5  # exactly one write of each id survived the race
    assert [r["id"] for r in journal_store._load_lines("fills.jsonl")] == ["0", "1", "2", "3", "4"]
