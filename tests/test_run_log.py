import json
from webull_api import run_log


def test_load_empty_when_no_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "nope"))
    assert run_log.load() == []


def test_append_and_last_for(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    assert run_log.append([{"key": "rsi2", "ts": "2026-07-08T21:00:00Z", "result": "ok"}]) == 1
    run_log.append([{"key": "rsi2", "ts": "2026-07-09T21:00:00Z", "result": "no_op"},
                    {"key": "other", "ts": "2026-07-09T21:00:00Z", "result": "ok"}])
    assert len(run_log.load()) == 3
    last = run_log.last_for("rsi2")
    assert last["ts"] == "2026-07-09T21:00:00Z" and last["result"] == "no_op"
    assert run_log.last_for("nope") is None


def test_load_skips_corrupt_lines(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    (tmp_path / "runs.jsonl").write_text(
        json.dumps({"key": "rsi2", "ts": "t"}) + "\n" + "{not json}\n", encoding="utf-8")
    assert len(run_log.load()) == 1
