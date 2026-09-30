from __future__ import annotations

import pytest

from webull_api.lab.schema import ProvingState, WalkForwardReport, ForwardRun, TrialBook
from webull_api.lab.gate_a import NotEnoughData
from webull_api.lab.trial import CorporateActionSeam
from webull_api.strategy.schema import Strategy
from webull_api.strategy.cost import NO_COST
from webull_api.strategy.backtest import run_backtest
from webull_api.lab import trial


def _strat(entry=None, **kw):
    entry = entry or {"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"}
    base = dict(name="t", symbol="SPY", entry=entry, starting_equity=10000.0)
    base.update(kw)
    return Strategy(**base)


def _bars(closes, start="2026-01-02"):
    """Build OHLC dicts on sequential ISO dates (one trading day apart, naive)."""
    import datetime as dt
    d = dt.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        out.append({"time": (d + dt.timedelta(days=i)).isoformat(),
                    "open": float(c), "high": float(c) + 1.0,
                    "low": float(c) - 1.0, "close": float(c), "volume": 1_000_000.0})
    return out


def _wfr():
    return WalkForwardReport(passed=True, pooled_expectancy=10.0, expected_trades_over_window=20.0)


# ---- warmup_for ----
def test_warmup_for_uses_max_period_plus_buffer():
    s = _strat(entry={"type": "sma_cross", "fast": 10, "slow": 50, "direction": "above"})
    assert trial.warmup_for(s) == 60  # 50 + warmup_buffer(10)


def test_warmup_for_spans_entry_exit_filter_leaves():
    s = _strat(
        entry={"type": "sma_cross", "fast": 5, "slow": 20, "direction": "above"},
        exit={"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"},
        filter={"type": "price_vs_sma", "period": 100, "side": "above"})
    assert trial.warmup_for(s) == 110  # max(20, 14, 100) + 10


def test_warmup_for_composite_takes_max_leaf():
    s = _strat(entry={"type": "all_of", "conditions": [
        {"type": "sma_cross", "fast": 2, "slow": 9, "direction": "above"},
        {"type": "rsi", "period": 40, "threshold": 30, "comparison": "below"}]})
    assert trial.warmup_for(s) == 50  # max(9, 40) + 10


# ---- open_trial ----
def test_open_trial_sets_forward_start_to_confirmed_length():
    s = _strat()                            # warmup_for == 13
    bars = _bars([100.0] * 20)
    state = trial.open_trial(s, {"SPY": bars}, trial_id="t1", now_iso="2026-02-01T00:00:00",
                             inception_et_date="2026-02-01", gate_a=_wfr(), basket=["SPY"])
    assert isinstance(state, ProvingState)
    assert state.status == "proving"
    assert state.forward_start_by_symbol["SPY"] == len(state.bars_by_symbol["SPY"])
    assert state.trial_id == "t1" and state.basket == ["SPY"]
    assert state.gate_a is not None and state.bars_observed == 0


def test_open_trial_drops_forming_bar_when_dated_today():
    s = _strat()
    bars = _bars([100.0] * 20, start="2026-02-01")   # last bar dated 2026-02-20
    last_date = bars[-1]["time"][:10]
    state = trial.open_trial(s, {"SPY": bars}, trial_id="t1", now_iso="x",
                             inception_et_date=last_date, gate_a=_wfr(), basket=["SPY"])
    assert len(state.bars_by_symbol["SPY"]) == len(bars) - 1   # forming bar dropped


def test_open_trial_raises_when_below_warmup():
    s = _strat(entry={"type": "sma_cross", "fast": 10, "slow": 50, "direction": "above"})
    bars = _bars([100.0] * 20)               # warmup_for == 60 > 20
    with pytest.raises(NotEnoughData):
        trial.open_trial(s, {"SPY": bars}, trial_id="t1", now_iso="x",
                         inception_et_date="2099-01-01", gate_a=_wfr(), basket=["SPY"])


# ---- forward_run faithfulness (THE anchor) ----
def test_forward_run_flat_ending_equals_run_backtest_trade_for_trade():
    # breakout entry + take-profit closes the trade well before the end -> position FLAT at the last
    # bar -> run_backtest's terminal force-close is a no-op -> the two engines must be identical.
    # Fixture note: close[3]=108 (< rolling_high[2]=110) keeps trig[3]=False so only ONE entry
    # fires and the position is flat for bars 4-5 — matching the "FLAT at last bar" intent.
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"}, take_profit_pct=10.0)
    bars = [
        {"time": "2026-01-02", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1e6},
        {"time": "2026-01-03", "open": 100, "high": 105, "low": 100, "close": 105, "volume": 1e6},
        {"time": "2026-01-04", "open": 100, "high": 110, "low": 100, "close": 110, "volume": 1e6},
        {"time": "2026-01-05", "open": 110, "high": 125, "low": 110, "close": 108, "volume": 1e6},
        {"time": "2026-01-06", "open": 120, "high": 121, "low": 119, "close": 120, "volume": 1e6},
        {"time": "2026-01-07", "open": 120, "high": 121, "low": 119, "close": 120, "volume": 1e6},
    ]
    fr = trial.forward_run(s, bars, 0, cost=NO_COST, starting_equity=10000.0)
    bt = run_backtest(s, bars)
    assert isinstance(fr, ForwardRun)
    assert fr.result.model_dump() == bt.model_dump()      # trade-for-trade + curve identical
    assert fr.forward_trades == bt.num_trades == 1
    assert fr.forward_bars == len(bars)


def test_forward_run_leaves_open_position_open_minus_terminal_force_close():
    # entry fires on the penultimate bar and never exits -> open at the last bar. run_backtest
    # force-closes (one end_of_data trade); forward_run leaves it open, marked to the last close.
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"})
    bars = [
        {"time": "2026-01-02", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1e6},
        {"time": "2026-01-03", "open": 100, "high": 105, "low": 100, "close": 105, "volume": 1e6},
        {"time": "2026-01-04", "open": 100, "high": 110, "low": 100, "close": 110, "volume": 1e6},
        {"time": "2026-01-05", "open": 110, "high": 112, "low": 110, "close": 111, "volume": 1e6},
    ]
    fr = trial.forward_run(s, bars, 0, cost=NO_COST, starting_equity=10000.0)
    bt = run_backtest(s, bars)
    assert fr.result.num_trades == 0                      # NOT force-closed
    assert bt.num_trades == 1 and bt.trades[-1].exit_reason == "end_of_data"
    # 90 sh @110 -> cash 100; marked to 111 -> 100 + 90*111 = 10090
    assert fr.result.equity_curve[-1].equity == pytest.approx(10090.0)


def test_forward_run_cost_reduces_return_vs_no_cost():
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"})
    bars = _bars([100, 100, 105, 110, 112, 109, 100, 100], start="2026-01-02")
    from dataclasses import replace
    from webull_api.strategy.cost import CostModel
    free = trial.forward_run(s, bars, 0, cost=NO_COST).result.total_return_pct
    costed = trial.forward_run(s, bars, 0, cost=CostModel(slippage_pct=0.5)).result.total_return_pct
    assert costed <= free


# ---- derive_book ----
def test_derive_book_wraps_forward_run_into_single_symbol_book():
    # Fixture note: close[3]=108 (< rolling_high[2]=110) prevents trig[3]=True so only ONE trade
    # fires, position is flat at bar 4, and run_backtest's force-close is a no-op -> models match.
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"}, take_profit_pct=10.0)
    bars = [
        {"time": "2026-01-02", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1e6},
        {"time": "2026-01-03", "open": 100, "high": 105, "low": 100, "close": 105, "volume": 1e6},
        {"time": "2026-01-04", "open": 100, "high": 110, "low": 100, "close": 110, "volume": 1e6},
        {"time": "2026-01-05", "open": 110, "high": 125, "low": 110, "close": 108, "volume": 1e6},
        {"time": "2026-01-06", "open": 120, "high": 121, "low": 119, "close": 120, "volume": 1e6},
    ]
    book = trial.derive_book(s, bars, 0, cost=NO_COST, starting_equity=10000.0)
    assert isinstance(book, TrialBook) and book.scope == "single"
    assert book.metrics.model_dump() == run_backtest(s, bars).model_dump()
    assert book.trades_by_symbol["SPY"] == book.metrics.trades
    assert "SPY" in book.per_symbol_metrics and book.forward_bars == len(bars)


# ---- derive_portfolio: portfolio curve == sum of sub-books ----
def test_derive_portfolio_curve_is_sum_of_sub_books():
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"})
    a = _bars([100, 100, 105, 110, 112, 109, 100], start="2026-01-02")
    b = _bars([50, 50, 55, 60, 62, 59, 50], start="2026-01-02")     # same time axis
    bars_by_symbol = {"AAA": a, "BBB": b}
    starts = {"AAA": 0, "BBB": 0}
    port = trial.derive_portfolio(s, bars_by_symbol, starts, cost=NO_COST, starting_equity=10000.0)
    assert isinstance(port, TrialBook) and port.scope == "portfolio"
    # per_equity = 10000/2 = 5000 each
    sub_a = trial.derive_book(trial._retarget(s, "AAA"), a, 0, cost=NO_COST, starting_equity=5000.0)
    sub_b = trial.derive_book(trial._retarget(s, "BBB"), b, 0, cost=NO_COST, starting_equity=5000.0)
    assert len(port.equity_curve) == len(sub_a.equity_curve) == len(sub_b.equity_curve)
    for k in range(len(port.equity_curve)):
        assert port.equity_curve[k].equity == pytest.approx(
            sub_a.equity_curve[k].equity + sub_b.equity_curve[k].equity)


def test_derive_portfolio_pools_trades_tagged_by_symbol():
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"})
    a = _bars([100, 100, 105, 110, 112, 109, 100], start="2026-01-02")
    b = _bars([50, 50, 55, 60, 62, 59, 50], start="2026-01-02")
    port = trial.derive_portfolio(s, {"AAA": a, "BBB": b}, {"AAA": 0, "BBB": 0},
                                  cost=NO_COST, starting_equity=10000.0)
    assert set(port.trades_by_symbol.keys()) == {"AAA", "BBB"}
    pooled = sum(len(v) for v in port.trades_by_symbol.values())
    assert port.metrics.num_trades == pooled                    # ledger pooled
    assert set(port.per_symbol_metrics.keys()) == {"AAA", "BBB"}


def _ohlc(date, c):
    return {"time": date, "open": float(c), "high": float(c) + 1, "low": float(c) - 1,
            "close": float(c), "volume": 1e6}


# ---- append_confirmed ----
def test_append_confirmed_drops_forming_bar_dated_today():
    have = [_ohlc("2026-03-02", 100)]
    fresh = [_ohlc("2026-03-03", 101), _ohlc("2026-03-04", 102), _ohlc("2026-03-05", 103)]
    new = trial.append_confirmed(have, fresh, today_et="2026-03-05")   # last bar is forming
    assert [b["time"] for b in new] == ["2026-03-03", "2026-03-04"]


def test_append_confirmed_keeps_all_when_last_bar_already_closed():
    have = [_ohlc("2026-03-02", 100)]
    fresh = [_ohlc("2026-03-03", 101), _ohlc("2026-03-04", 102)]
    new = trial.append_confirmed(have, fresh, today_et="2026-03-09")   # nothing forming today
    assert [b["time"] for b in new] == ["2026-03-03", "2026-03-04"]


def test_append_confirmed_idempotent_when_nothing_newer():
    have = [_ohlc("2026-03-02", 100), _ohlc("2026-03-03", 101)]
    fresh = [_ohlc("2026-03-02", 100), _ohlc("2026-03-03", 101)]       # already held
    assert trial.append_confirmed(have, fresh, today_et="2026-03-09") == []


def test_append_confirmed_raises_on_non_monotonic_batch():
    have = [_ohlc("2026-03-02", 100)]
    fresh = [_ohlc("2026-03-04", 102), _ohlc("2026-03-03", 101)]       # out of order
    with pytest.raises(ValueError):
        trial.append_confirmed(have, fresh, today_et="2026-03-09")


def test_append_confirmed_detects_corporate_action_seam_on_overlap_mismatch():
    have = [_ohlc("2026-03-02", 100), _ohlc("2026-03-03", 200)]        # stored close 200 at 03-03
    fresh = [_ohlc("2026-03-03", 100), _ohlc("2026-03-04", 101)]       # re-fetched 03-03 close 100 (2:1 split)
    with pytest.raises(CorporateActionSeam):
        trial.append_confirmed(have, fresh, today_et="2026-03-09")


def _proving_state(forward_strat):
    """A single-symbol ('SPY') trial whose forward window is empty (forward_start == warmup len)."""
    warm = _bars([100.0] * 14, start="2026-01-02")     # >= warmup_for(13)
    return trial.open_trial(forward_strat, {"SPY": warm}, trial_id="t1",
                            now_iso="2026-01-20T00:00:00", inception_et_date="2099-01-01",
                            gate_a=_wfr(), basket=["SPY"])


# ---- advance: catch-up byte-identical (THE second anchor) ----
def test_advance_catch_up_byte_identical_book():
    s = _strat(entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})
    fwd = _bars([101, 102, 103, 104, 105], start="2026-01-16")   # 5 forward closes (post-warmup dates)

    # Path A: one advance with all 5 bars at once.
    sa = _proving_state(s)
    sa, na = trial.advance(sa, {"SPY": fwd}, now_iso="2026-01-25T00:00:00",
                           today_et="2099-01-01", cost=NO_COST)

    # Path B: five advances, one bar each.
    sb = _proving_state(s)
    total = 0
    for k in range(5):
        sb, nk = trial.advance(sb, {"SPY": [fwd[k]]}, now_iso="2026-01-25T00:00:00",
                               today_et="2099-01-01", cost=NO_COST)
        total += nk

    assert na == total == 5
    assert sa.book is not None and sb.book is not None
    assert sa.book.model_dump() == sb.book.model_dump()          # byte-identical derived book
    assert sa.bars_by_symbol["SPY"] == sb.bars_by_symbol["SPY"]


def test_advance_idempotent_no_op_when_nothing_advanced():
    s = _strat(entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})
    state = _proving_state(s)
    # advance once to seed a book
    state, _ = trial.advance(state, {"SPY": _bars([101, 102], start="2026-01-16")},
                             now_iso="x", today_et="2099-01-01", cost=NO_COST)
    book_before = state.book.model_dump()
    # re-advance with bars we already hold -> no new confirmed bar
    state, n = trial.advance(state, {"SPY": _bars([101, 102], start="2026-01-16")},
                             now_iso="y", today_et="2099-01-01", cost=NO_COST)
    assert n == 0
    assert state.book.model_dump() == book_before                # unchanged


def test_advance_reconstructs_across_multi_week_gap():
    s = _strat(entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})
    state = _proving_state(s)
    # a contiguous batch that jumps a multi-week gap (Jan 16 -> Feb 10) — strictly monotonic, complete
    fwd = (_bars([101, 102], start="2026-01-16") + _bars([110, 111, 112], start="2026-02-10"))
    state, n = trial.advance(state, {"SPY": fwd}, now_iso="z", today_et="2099-01-01", cost=NO_COST)
    assert n == 5
    assert state.bars_observed == state.book.forward_bars == 5    # bar-complete forward window


def test_advance_records_coverage_gap_on_corporate_action_seam():
    s = _strat(entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})
    state = _proving_state(s)
    state, _ = trial.advance(state, {"SPY": _bars([101, 102], start="2026-01-16")},
                             now_iso="x", today_et="2099-01-01", cost=NO_COST)
    held = list(state.bars_by_symbol["SPY"])
    # re-fetch the last held bar with a restated close -> seam -> recorded, that symbol not appended
    seam = [{"time": held[-1]["time"], "open": 60, "high": 61, "low": 59, "close": 60, "volume": 1e6},
            {"time": "2026-01-20", "open": 60, "high": 61, "low": 59, "close": 60, "volume": 1e6}]
    state, n = trial.advance(state, {"SPY": seam}, now_iso="x", today_et="2099-01-01", cost=NO_COST)
    assert n == 0
    assert any(g.get("type") == "corp_action_seam" for g in state.coverage_gaps)


# ---- Fix: forward Trade.pnl nets the round-trip commission exactly like backtest.py ----
def test_forward_trade_pnl_nets_round_trip_commission():
    from webull_api.strategy.cost import CostModel, commission
    s = _strat(entry={"type": "breakout", "lookback": 2, "direction": "high"}, take_profit_pct=10.0)
    bars = [
        {"time": "2026-01-02", "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1e6},
        {"time": "2026-01-03", "open": 100, "high": 105, "low": 100, "close": 105, "volume": 1e6},
        {"time": "2026-01-04", "open": 100, "high": 110, "low": 100, "close": 110, "volume": 1e6},
        {"time": "2026-01-05", "open": 110, "high": 125, "low": 110, "close": 108, "volume": 1e6},
        {"time": "2026-01-06", "open": 120, "high": 121, "low": 119, "close": 120, "volume": 1e6},
    ]
    cost = CostModel(commission_flat=1.0)
    fr = trial.forward_run(s, bars, 0, cost=cost, starting_equity=10000.0)
    assert fr.forward_trades == 1
    t = fr.result.trades[0]
    fee = 2 * commission(t.shares, cost)
    assert fee > 0
    # gross-of-commission before the fix; now net, matching backtest.close_position
    assert t.pnl == pytest.approx((t.exit_price - t.entry_price) * t.shares - fee)
