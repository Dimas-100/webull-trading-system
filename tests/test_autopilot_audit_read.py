import json
from webull_api.autopilot import audit


def test_placed_count_zero_when_no_store(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "nope"))
    assert audit.placed_count() == 0


def test_placed_count_counts_only_placed_true(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    logdir = tmp_path / "log"
    logdir.mkdir()
    (logdir / "2026-07-08.jsonl").write_text(
        json.dumps({"symbol": "A", "placed": True}) + "\n"
        + json.dumps({"symbol": "B", "placed": False}) + "\n", encoding="utf-8")
    (logdir / "2026-07-09.jsonl").write_text(
        json.dumps({"symbol": "C", "placed": True}) + "\n"
        + "\n"  # tolerate a blank line
        + "{not json}\n"  # tolerate a corrupt line
        + json.dumps({"placed": 1}) + "\n"  # truthy int, not True
        + json.dumps({"placed": "true"}) + "\n"  # truthy string, not True
        + json.dumps({"symbol": "E"}) + "\n"  # missing placed key
        + "42\n",  # valid JSON but not a dict -- tolerated, not counted
        encoding="utf-8")
    assert audit.placed_count() == 2
