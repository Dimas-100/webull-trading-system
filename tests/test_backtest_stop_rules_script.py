"""Script-level test with an injected bar fetcher -- no network. Mirrors
test_backtest_rsi2_script.py's fixture shape (a dip-then-rise round trip on AAA, flat BBB)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backtest_stop_rules  # noqa: E402

CELL_NAMES = ("base", "backstop8", "be1", "be2", "be3", "be5")


def _sdk_rows(prices, start_day=1):
    """Newest-first SDK-shaped rows (to_ohlcv reverses). `prices` is (open, close) pairs;
    high/low bracket the two so no wick ever crosses a stop level in this fixture."""
    rows = [{"time": f"2026-01-{start_day + i:02d}", "open": str(o), "high": str(max(o, c)),
             "low": str(min(o, c)), "close": str(c), "volume": "1"}
            for i, (o, c) in enumerate(prices)]
    return list(reversed(rows))


def _fake_get_bars(sym, tf, count):
    dip = [(100, 100)] * 7 + [(95, 94), (95, 95), (96, 97), (99, 101), (104, 104)]
    flat = [(100, 100)] * 12
    return _sdk_rows(dip if sym == "AAA" else flat)


def test_script_writes_every_cell_and_the_verdict_line(tmp_path):
    rc = backtest_stop_rules.run(
        ["--universe", "AAA,BBB", "--out-dir", str(tmp_path)],
        get_bars=_fake_get_bars, today="2026-09-08")
    assert rc == 0
    md = tmp_path / "2026-09-08-rsi2-stop-rules.md"
    js = tmp_path / "2026-09-08-rsi2-stop-rules.json"
    assert md.exists() and js.exists()

    text = md.read_text(encoding="utf-8")
    assert backtest_stop_rules.DECISION_RULE in text
    for name in CELL_NAMES:
        assert f"| {name} |" in text
    assert "CANDIDATE" in text or "DISMISS" in text

    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["decision_rule"] == backtest_stop_rules.DECISION_RULE
    assert set(data["cells"]) == set(CELL_NAMES)
    assert data["cells"]["base"]["trades"] >= 1
    # base/backstop8 are inputs to the rule, never judged themselves.
    assert data["cells"]["base"]["verdict"] == ""
    assert data["cells"]["backstop8"]["verdict"] == ""
    for name in ("be1", "be2", "be3", "be5"):
        assert data["cells"][name]["verdict"] in ("CANDIDATE", "DISMISS")
        assert "exit_reasons" in data["cells"][name]


def test_verdict_matches_the_decision_rule_both_conditions():
    # Hand-check _verdict against the pre-registered rule text: >= expectancy AND no deeper
    # drawdown (drawdown is a <=0 percentage -- "no deeper" is the algebraic >=, see NOTE in
    # the script's module docstring).
    backstop8 = {"expectancy_pct": 1.0, "max_drawdown_pct": -10.0}
    assert backtest_stop_rules._verdict(
        {"expectancy_pct": 1.0, "max_drawdown_pct": -10.0}, backstop8) == "CANDIDATE"
    assert backtest_stop_rules._verdict(
        {"expectancy_pct": 1.5, "max_drawdown_pct": -5.0}, backstop8) == "CANDIDATE"  # better both
    assert backtest_stop_rules._verdict(
        {"expectancy_pct": 0.9, "max_drawdown_pct": -5.0}, backstop8) == "DISMISS"    # expectancy worse
    assert backtest_stop_rules._verdict(
        {"expectancy_pct": 1.5, "max_drawdown_pct": -12.0}, backstop8) == "DISMISS"   # deeper drawdown


def test_source_tiingo_uses_the_offline_adapter(tmp_path, monkeypatch):
    from webull_api.tiingo import store
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    rows = [{"date": f"2025-{m:02d}-15T00:00:00.000Z", "open": 10, "high": 11, "low": 9,
            "close": 10.5, "volume": 1, "adjOpen": 10, "adjHigh": 11, "adjLow": 9,
            "adjClose": 10.5, "adjVolume": 1, "divCash": 0, "splitFactor": 1}
           for m in range(1, 13)]
    for sym in ("SPY", "AAA"):
        store.write(sym, store.from_api(rows))
    rc = backtest_stop_rules.run(
        ["--universe", "AAA", "--count", "5000", "--source", "tiingo", "--out-dir", str(tmp_path)],
        today="2026-09-08")
    assert rc == 0
    md_path = tmp_path / "2026-09-08-rsi2-stop-rules.md"
    js_path = tmp_path / "2026-09-08-rsi2-stop-rules.json"
    assert md_path.exists() and js_path.exists()
    data = json.loads(js_path.read_text(encoding="utf-8"))
    assert data["source"] == "tiingo"


def test_fatal_when_spy_missing(tmp_path):
    def no_spy(sym, tf, count):
        if sym == "SPY":
            raise RuntimeError("boom")
        return _fake_get_bars(sym, tf, count)

    rc = backtest_stop_rules.run(["--universe", "AAA", "--out-dir", str(tmp_path)],
                                 get_bars=no_spy, today="2026-09-08")
    assert rc == 1
    assert not any(tmp_path.iterdir())
