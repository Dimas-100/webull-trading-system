"""scanner_store — snapshots + scores JSONL logs (dedup by id, corrupt lines skipped)."""
import json

from webull_web import scanner_store


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBULL_SCANNER_DIR", str(tmp_path))


def test_snapshots_append_dedup_and_roundtrip(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    a = {"id": "2026-07-21:AAPL", "date": "2026-07-21", "symbol": "AAPL"}
    assert scanner_store.append_snapshots([a, a]) == 1
    assert scanner_store.append_snapshots([a]) == 0
    assert [r["id"] for r in scanner_store.load_snapshots()] == ["2026-07-21:AAPL"]


def test_scores_are_a_separate_log(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    scanner_store.append_snapshots([{"id": "S"}])
    assert scanner_store.append_scores([{"id": "S"}]) == 1   # same id, different file — both live
    assert len(scanner_store.load_snapshots()) == 1
    assert len(scanner_store.load_scores()) == 1


def test_load_skips_corrupt_lines(tmp_path, monkeypatch):
    _use_tmp(monkeypatch, tmp_path)
    scanner_store.append_scores([{"id": "A"}])
    f = tmp_path / "scores.jsonl"
    f.write_text(f.read_text(encoding="utf-8") + "{corrupt\n" + json.dumps({"id": "B"}) + "\n",
                 encoding="utf-8")
    assert [r["id"] for r in scanner_store.load_scores()] == ["A", "B"]
