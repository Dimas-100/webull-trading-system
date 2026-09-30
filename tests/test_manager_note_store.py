"""Manager's-note JSONL store: sorted load, corrupt-line skip (netliq_store pattern)."""
import inspect
import json

from webull_web import manager_notes_store as store


def test_load_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    assert store.load() == []


def test_append_and_sorted_load(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    store.append({"date": "2026-07-11", "ts": "2026-07-11T21:32:00", "text": "b", "lines": ["b"]})
    store.append({"date": "2026-07-10", "ts": "2026-07-10T21:32:00", "text": "a", "lines": ["a"]})
    rows = store.load()
    assert [r["date"] for r in rows] == ["2026-07-10", "2026-07-11"]


def test_corrupt_lines_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    store.append({"date": "2026-07-10", "ts": "t", "text": "a", "lines": []})
    with (tmp_path / "manager_notes.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{nope}\n[1]\n\n")
    assert len(store.load()) == 1


def test_store_has_no_submit_path():
    src = inspect.getsource(store)
    for forbidden in ("trading.place", "import trading", "place_order", "confirm=True"):
        assert forbidden not in src
