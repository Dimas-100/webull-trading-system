# tests/test_autopilot_audit.py
import json

from webull_api.autopilot import audit


def test_log_decision_appends_jsonl(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    audit.log_decision({"symbol": "AAPL", "placed": True}, day="2026-07-07")
    audit.log_decision({"symbol": "MSFT", "placed": False}, day="2026-07-07")
    lines = (tmp_path / "log" / "2026-07-07.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["symbol"] == "AAPL"
    assert json.loads(lines[1])["placed"] is False


def test_notify_stdout_only_returns_true(capsys):
    ok = audit.notify("2 placed, 1 skipped", target="")
    assert ok is True
    assert "2 placed" in capsys.readouterr().out


def test_notify_never_raises_on_bad_target():
    # An unreachable/invalid URL must be swallowed, not raised.
    assert audit.notify("x", target="http://127.0.0.1:0/nope") is False
