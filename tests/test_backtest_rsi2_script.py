"""Script-level test with an injected bar fetcher — no network."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import backtest_rsi2  # noqa: E402


def _sdk_rows(prices, start_day=1):
    """Newest-first SDK-shaped rows (to_ohlcv reverses)."""
    rows = [{"time": f"2026-01-{start_day + i:02d}", "open": str(o), "high": str(max(o, c)),
             "low": str(min(o, c)), "close": str(c), "volume": "1"}
            for i, (o, c) in enumerate(prices)]
    return list(reversed(rows))


def _fake_get_bars(sym, tf, count):
    dip = [(100, 100)] * 5 + [(99, 98), (97, 96), (95, 94), (95, 97), (98, 100),
                              (101, 103), (104, 104)]
    flat = [(100, 100)] * 12
    return _sdk_rows(dip if sym == "AAA" else flat)


def test_script_writes_report_and_json(tmp_path):
    rc = backtest_rsi2.run(
        ["--universe", "AAA,BBB", "--out-dir", str(tmp_path)],
        get_bars=_fake_get_bars, today="2026-07-28")
    assert rc == 0
    md = tmp_path / "2026-07-28-rsi2-backtest.md"
    js = tmp_path / "2026-07-28-rsi2-backtest.json"
    assert md.exists() and js.exists()
    text = md.read_text(encoding="utf-8")
    assert "AAA" in text and "Selection bias" in text
    data = json.loads(js.read_text(encoding="utf-8"))
    assert data["stats"]["overall"]["trades"] >= 1
    assert data["universe"] == ["AAA", "BBB"]
    assert data["source"] == "webull"


def test_script_fatal_when_spy_missing(tmp_path):
    def no_spy(sym, tf, count):
        if sym == "SPY":
            raise RuntimeError("boom")
        return _fake_get_bars(sym, tf, count)

    rc = backtest_rsi2.run(["--universe", "AAA", "--out-dir", str(tmp_path)],
                           get_bars=no_spy, today="2026-07-28")
    assert rc == 1
    assert not (tmp_path / "2026-07-28-rsi2-backtest.md").exists()
    assert not (tmp_path / "2026-07-28-rsi2-backtest.json").exists()


def test_script_fatal_when_universe_all_failed(tmp_path):
    """Every universe fetch fails (SPY healthy) -> the empty-data fatal branch, nothing written."""
    def only_spy(sym, tf, count):
        if sym == "SPY":
            return _fake_get_bars("BBB", tf, count)
        raise RuntimeError("no data")

    rc = backtest_rsi2.run(["--universe", "AAA", "--out-dir", str(tmp_path)],
                           get_bars=only_spy, today="2026-07-28")
    assert rc == 1
    assert not any(tmp_path.iterdir())


def test_entitlement_error_propagates(tmp_path):
    """MarketDataNotEntitledError is account-wide — never a per-symbol degrade (spec)."""
    import pytest
    from webull_api.market_data import MarketDataNotEntitledError

    def entitle_boom(sym, tf, count):
        raise MarketDataNotEntitledError("401 insufficient permission")

    with pytest.raises(MarketDataNotEntitledError):
        backtest_rsi2.run(["--universe", "AAA", "--out-dir", str(tmp_path)],
                          get_bars=entitle_boom, today="2026-07-28")
    assert not any(tmp_path.iterdir())


def test_spy_depth_probe_falls_back_to_standard_count(tmp_path):
    """Webull rejects counts above its cap — the extended SPY probe must fall back."""
    calls = []

    def capped(sym, tf, count):
        calls.append((sym, count))
        if sym == "SPY" and count == "1450":
            raise RuntimeError("ServerException")
        return _fake_get_bars(sym, tf, count)

    rc = backtest_rsi2.run(["--universe", "AAA", "--out-dir", str(tmp_path)],
                           get_bars=capped, today="2026-07-28")
    assert rc == 0
    assert ("SPY", "1450") in calls and ("SPY", "1200") in calls


def test_report_carries_warmup_row_and_disclosures(tmp_path):
    rc = backtest_rsi2.run(
        ["--universe", "AAA,BBB", "--out-dir", str(tmp_path)],
        get_bars=_fake_get_bars, today="2026-07-28")
    assert rc == 0
    text = (tmp_path / "2026-07-28-rsi2-backtest.md").read_text(encoding="utf-8")
    assert "| warmup |" in text
    assert "Price-only data" in text and "Idle cash earns 0%" in text
    assert "Excluded list not applied" in text
    data = json.loads((tmp_path / "2026-07-28-rsi2-backtest.json").read_text(encoding="utf-8"))
    assert "warmup" in data["stats"] and "equity_curve" in data


def test_source_tiingo_uses_the_offline_adapter(tmp_path, monkeypatch):
    from webull_api.tiingo import store
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    rows = [{"date": f"2025-{m:02d}-15T00:00:00.000Z", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1,
             "adjOpen": 10, "adjHigh": 11, "adjLow": 9, "adjClose": 10.5, "adjVolume": 1, "divCash": 0, "splitFactor": 1}
            for m in range(1, 13)]
    for sym in ("SPY", "AAA"):
        store.write(sym, store.from_api(rows))
    rc = backtest_rsi2.run(
        ["--universe", "AAA", "--count", "5000", "--source", "tiingo", "--out-dir", str(tmp_path)],
        today="2026-09-07")
    assert rc == 0
    md_path = tmp_path / "2026-09-07-rsi2-backtest-tiingo.md"
    js_path = tmp_path / "2026-09-07-rsi2-backtest-tiingo.json"
    assert md_path.exists() and js_path.exists()
    # a same-day webull run must never collide with these tiingo-suffixed names
    assert not (tmp_path / "2026-09-07-rsi2-backtest.md").exists()
    assert not (tmp_path / "2026-09-07-rsi2-backtest.json").exists()
    md = md_path.read_text(encoding="utf-8")
    assert "tiingo" in md.lower() and "dividend-adjusted" in md.lower()
    # M1: the tiingo path names its span a CHOSEN window (the --count), not Webull's depth cap.
    assert "Span is the requested `--count` bars from the Tiingo store" in md
    assert "Span is whatever Webull's history depth returns" not in md
    data = json.loads(js_path.read_text(encoding="utf-8"))
    assert data["source"] == "tiingo"
