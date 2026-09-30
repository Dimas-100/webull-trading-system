import pytest

from webull_api import exits
from webull_api.exits import ExitConfig, broke_below_sma, evaluate, scan
from webull_api.market_data import MarketDataNotEntitledError


# ---- broke_below_sma ----
def test_broke_below_sma_fires_on_cross():
    closes = [10.0] * 20 + [9.0]            # prior bar at/above SMA20, latest dips below
    assert broke_below_sma(closes) is True


def test_broke_below_sma_false_when_already_below():
    closes = [10.0] * 19 + [8.0, 7.0]       # prior bar already below its SMA20 -> not a fresh cross
    assert broke_below_sma(closes) is False


def test_broke_below_sma_false_when_short_series():
    assert broke_below_sma([1.0] * 5) is False


def test_broke_below_sma_fires_on_established_break():
    # Missed-cross catch-up: the cross happened days ago (not fresh), and the last 3 closes
    # all sit below their SMA20 -> the state rule fires (the SW 2026-07-08 drift class).
    closes = [10.0] * 22 + [7.0, 7.0, 7.0]
    assert broke_below_sma(closes) is True


def test_broke_below_sma_state_needs_three_closes_below():
    # Two closes below (third-last still at its average) is a dip, not an established break.
    closes = [10.0] * 23 + [7.0, 7.0]
    assert broke_below_sma(closes) is False


# ---- evaluate ----
def test_evaluate_stop_fires_at_threshold():
    sig = evaluate("AAPL", 10, 100.0, 92.0, None, [], ExitConfig())   # -8.0% exactly
    assert sig is not None and sig.reason == "stop" and sig.reasons == ["stop"]


def test_evaluate_target_fires_at_threshold():
    sig = evaluate("AAPL", 10, 100.0, 120.0, None, [], ExitConfig())  # +20.0% exactly
    assert sig is not None and sig.reason == "target"


def test_evaluate_none_in_band():
    assert evaluate("AAPL", 10, 100.0, 105.0, None, [], ExitConfig()) is None


def test_evaluate_break_only():
    closes = [10.0] * 20 + [9.0]
    sig = evaluate("AAPL", 10, 100.0, 100.0, None, closes, ExitConfig())  # 0% P&L, but trend break
    assert sig is not None and sig.reason == "break" and sig.reasons == ["break"]


def test_evaluate_break_fires_on_established_break_without_cross():
    # In-band P&L, no fresh cross, but 3 consecutive closes below SMA20 -> "break".
    closes = [10.0] * 22 + [7.0, 7.0, 7.0]
    sig = evaluate("SW", 110, 7.2, 7.0, None, closes, ExitConfig())   # -2.8%: stop/target silent
    assert sig is not None and sig.reason == "break" and sig.reasons == ["break"]


def test_evaluate_priority_target_then_break():
    closes = [10.0] * 20 + [9.0]
    sig = evaluate("AAPL", 10, 100.0, 125.0, None, closes, ExitConfig())  # +25% AND a break
    assert sig.reason == "target" and sig.reasons == ["target", "break"]


def test_evaluate_upl_rate_fallback():
    sig = evaluate("AAPL", 10, None, None, -0.10, [], ExitConfig())   # no last/cost; rate -10% -> stop
    assert sig is not None and sig.reason == "stop" and sig.last is None


# ---- scan ----
def _pos(symbol="AAPL", qty="10", cost="100", last="92", rate=None, it="EQUITY"):
    d = {"symbol": symbol, "quantity": qty, "cost_price": cost, "last_price": last, "instrument_type": it}
    if rate is not None:
        d["unrealized_profit_loss_rate"] = rate
    return d


def test_scan_skips_zero_qty_and_options():
    positions = [_pos("ZERO", qty="0"), _pos("OPT", it="OPTION"), _pos("NVDA", last="90")]
    rows = scan("ACC", get_positions=lambda a: positions, get_bars=lambda *a, **k: [])
    assert [r.symbol for r in rows] == ["NVDA"]


def test_scan_signals_first():
    positions = [_pos("HOLD", last="105"), _pos("STOP", last="90")]
    rows = scan("ACC", get_positions=lambda a: positions, get_bars=lambda *a, **k: [])
    assert rows[0].symbol == "STOP" and rows[0].signal is not None
    assert rows[1].symbol == "HOLD" and rows[1].signal is None and rows[1].held == "no exit signal"


def test_scan_bar_error_degrades_break_only():
    def bars(*a, **k):
        raise RuntimeError("bar outage")
    rows = scan("ACC", get_positions=lambda a: [_pos("STOP", last="90")], get_bars=bars)
    assert rows[0].signal is not None and rows[0].signal.reason == "stop"   # stop still evaluated


def test_scan_entitlement_propagates():
    def bars(*a, **k):
        raise MarketDataNotEntitledError("no")
    with pytest.raises(MarketDataNotEntitledError):
        scan("ACC", get_positions=lambda a: [_pos("AAPL")], get_bars=bars)


def test_scan_position_error_becomes_row(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(exits, "evaluate", boom)
    rows = scan("ACC", get_positions=lambda a: [_pos("BAD")], get_bars=lambda *a, **k: [])
    assert rows[0].error == "boom" and rows[0].signal is None


def test_scan_uses_upl_rate_when_no_last():
    pos = {"symbol": "AAPL", "quantity": "10", "unrealized_profit_loss_rate": "-0.10"}  # no last/cost
    rows = scan("ACC", get_positions=lambda a: [pos], get_bars=lambda *a, **k: [])
    assert rows[0].signal is not None and rows[0].signal.reason == "stop" and rows[0].signal.last is None
