from scripts.queue_decision import main
from webull_api import decisions_exec


def test_equity_sell_row(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_NOTIFY", "")
    assert main(["equity-sell", "FBTC", "--qty", "ALL", "--trigger", "green_day"]) == 0
    rows, errs = decisions_exec.load()
    assert errs == [] and rows[0]["side"] == "SELL" and rows[0]["order_type"] == "MARKET"
    assert "queued" in capsys.readouterr().out


def test_option_buy_row(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_NOTIFY", "")
    assert main(["option-buy", "F", "--expiry", "2026-09-18", "--strike", "14",
                 "--right", "C", "--qty", "1", "--limit", "0.50"]) == 0
    rows, _ = decisions_exec.load()
    assert rows[0]["asset"] == "OPTION" and rows[0]["option"]["strike"] == 14.0
    assert rows[0]["expires"]  # defaulted to today


def test_rsi2_above_is_queueable_with_a_threshold(monkeypatch, tmp_path):
    """The CLI whitelists trigger kinds; rsi2_above must be reachable or the exit can't be armed."""
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_NOTIFY", "")
    assert main(["equity-sell", "AAPL", "--qty", "ALL",
                                "--trigger", "rsi2_above", "--threshold", "70"]) == 0
    rows, _ = decisions_exec.load()
    assert rows[-1]["trigger"] == {"kind": "rsi2_above", "threshold": 70.0}
    assert rows[-1]["symbol"] == "AAPL" and rows[-1]["side"] == "SELL"


def test_rsi2_above_defaults_to_no_explicit_threshold(monkeypatch, tmp_path):
    """Omitting --threshold leaves the key off; the evaluator's own default (70) then applies."""
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_NOTIFY", "")
    assert main(["equity-sell", "AAPL", "--qty", "ALL",
                                "--trigger", "rsi2_above"]) == 0
    rows, _ = decisions_exec.load()
    assert rows[-1]["trigger"] == {"kind": "rsi2_above"}
