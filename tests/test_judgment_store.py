"""judgment_store: append-only JSONL round-trip under a temp ACTIVITY_DIR."""
import importlib


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    from webull_web import judgment_store
    importlib.reload(judgment_store)
    return judgment_store


def test_append_load_roundtrip(monkeypatch, tmp_path):
    js = _reload(monkeypatch, tmp_path)
    rows = [{"ts": "2026-08-11T18:00:00-04:00", "date": "2026-08-11", "symbol": "V",
             "sleeve": "rsi2", "order_ref": "abc123", "verdict": "proceed",
             "reasons": ["no event risk"], "confidence": 0.8, "model": "opus",
             "latency_ms": 1200}]
    assert js.append(rows) == 1
    got = js.load()
    assert got == rows
    assert (tmp_path / "judgment_verdicts.jsonl").exists()


def test_load_missing_and_corrupt(monkeypatch, tmp_path):
    js = _reload(monkeypatch, tmp_path)
    assert js.load() == []
    (tmp_path / "judgment_verdicts.jsonl").write_text('{"ok": 1}\nnot json\n', encoding="utf-8")
    assert js.load() == [{"ok": 1}]          # bad lines skipped, never raised


def test_today_rows_filters(monkeypatch, tmp_path):
    js = _reload(monkeypatch, tmp_path)
    js.append([{"date": "2026-08-10", "symbol": "A"}, {"date": "2026-08-11", "symbol": "B"}])
    assert [r["symbol"] for r in js.today_rows("2026-08-11")] == ["B"]
