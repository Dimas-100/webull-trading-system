import inspect
from webull_web import scorecard_store


def test_round_trip_sorted(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "activity"))
    scorecard_store.append({"date": "2026-07-25", "ts": "2026-07-25T17:30:00-04:00", "flags": []})
    scorecard_store.append({"date": "2026-07-18", "ts": "2026-07-18T17:30:00-04:00", "flags": []})
    rows = scorecard_store.load()
    assert [r["date"] for r in rows] == ["2026-07-18", "2026-07-25"]


def test_load_missing_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "activity"))
    assert scorecard_store.load() == []
    (tmp_path / "activity").mkdir()
    (tmp_path / "activity" / "scorecard_history.jsonl").write_text(
        '{"date": "2026-07-18"}\nnope\n', encoding="utf-8")
    assert [r["date"] for r in scorecard_store.load()] == ["2026-07-18"]


def test_store_has_no_submit_path():
    src = inspect.getsource(scorecard_store)
    assert "trading" not in src and "place" not in src
