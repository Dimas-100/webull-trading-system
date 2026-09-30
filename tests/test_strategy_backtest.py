import pytest
from webull_api.strategy.schema import Strategy
from webull_api.strategy.backtest import run_backtest


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 0}


def _flat_bars(closes):
    # open=high=low=close so only the signal (never a stop/target) drives exits
    return [_bar(f"t{i}", c, c, c, c) for i, c in enumerate(closes)]


def _strat(**kw):
    base = dict(name="x", symbol="X",
                entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                sizing={"type": "pct_equity", "value": 100}, starting_equity=10000)
    base.update(kw)
    return Strategy(**base)


def _run(strategy, closes):
    # mirrors the file's existing bar-fixture + run_backtest call pattern for a plain closes list
    return run_backtest(strategy, _flat_bars(closes))


def test_entry_fills_next_open_close_at_end():
    # closes cross 2-SMA above 3-SMA at bar 4 -> enter at bar 5 open (=4), exit end at bar 6 close (=5)
    res = run_backtest(_strat(), _flat_bars([1, 2, 1, 2, 3, 4, 5]))
    assert res.num_trades == 1
    t = res.trades[0]
    assert t.entry_time == "t5" and t.entry_price == pytest.approx(4.0)
    assert t.exit_time == "t6" and t.exit_price == pytest.approx(5.0)
    assert t.exit_reason == "end_of_data"
    assert res.total_return_pct == pytest.approx(25.0)   # 2500 sh * (5-4) on 10000


def test_stop_loss_exits_intrabar_at_stop_price():
    bars = _flat_bars([1, 2, 1, 2, 3, 4, 4])
    bars[6] = _bar("t6", 4, 4, 3.5, 4)   # bar 6 low pierces the 10% stop (3.6)
    res = run_backtest(_strat(stop_loss_pct=10), bars)
    t = res.trades[0]
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(3.6)
    assert res.total_return_pct == pytest.approx(-10.0)


def test_take_profit_exits_intrabar_at_target():
    bars = _flat_bars([1, 2, 1, 2, 3, 4, 4])
    bars[6] = _bar("t6", 4, 4.8, 4, 4)   # bar 6 high reaches the 10% target (4.4)
    res = run_backtest(_strat(take_profit_pct=10), bars)
    t = res.trades[0]
    assert t.exit_reason == "target" and t.exit_price == pytest.approx(4.4)


def test_no_signal_no_trade_and_buy_hold_reported():
    res = run_backtest(_strat(), _flat_bars([5, 5, 5, 5, 5]))
    assert res.num_trades == 0
    assert res.total_return_pct == pytest.approx(0.0)
    assert res.buy_hold_return_pct == pytest.approx(0.0)


def test_buy_hold_measured_from_first_tradable_bar():
    # Fix: B&H previously spanned the FULL window including the indicator warmup, where the
    # strategy cannot trade. sma_cross(2,3) -> first tradable index 3, so the pre-warmup
    # 1 -> 4 pop must not count: B&H = 5/4 - 1, not 5/1 - 1.
    res = run_backtest(_strat(), _flat_bars([1, 2, 4, 4, 4, 4, 5]))
    assert res.buy_hold_return_pct == pytest.approx(25.0)


def test_stale_pending_exit_never_closes_a_later_position():
    # Fix: an exit signal at t7 targets t8, but t8's open is MISSING and the stop closes the
    # position intrabar at t8 instead. The surviving pending_exit must be VOIDED by that close —
    # it used to fire on the NEXT position (entered t11) at its first open.
    bars = _flat_bars([1, 2, 1, 2, 3, 4, 3.8, 3.5, 2, 3, 4, 4.5, 5])
    bars[8] = _bar("t8", None, 2, 1.9, 2)          # missing open + stop-piercing low
    strat = _strat(exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"},
                   stop_loss_pct=15)
    res = run_backtest(strat, bars)
    assert [t.exit_reason for t in res.trades] == ["stop", "end_of_data"]
    t2 = res.trades[1]
    assert t2.entry_time == "t11" and t2.exit_time == "t12"    # NOT signal-closed at its own open
    # the replay stepper shares the rule (its _close also voids pending_exit)
    from webull_api.strategy.replay import strict_run
    sr = strict_run(strat, bars)
    assert [t.model_dump() for t in sr.trades] == [t.model_dump() for t in res.trades]


def test_end_of_data_close_tolerates_none_final_close():
    # Fix: a None final close (degraded bar) used to raise TypeError in the terminal force-close.
    bars = _flat_bars([1, 2, 1, 2, 3, 4, 5]) + [_bar("t7", None, None, None, None)]
    res = run_backtest(_strat(), bars)             # must not raise
    t = res.trades[-1]
    assert t.exit_reason == "end_of_data"
    assert t.exit_price == pytest.approx(5.0)      # falls back to the last non-None close
    assert t.exit_time == "t7"


def test_composite_wrapper_matches_bare_leaf_at_warmup_boundary():
    # Fix: an UNDEFINED prior state must not count as False. RSI(3) is first defined ALREADY
    # below 30 here (index 3, value 0): the bare leaf (legacy path, needs r[i-1] too) does not
    # fire, but a composite used to fire a phantom entry at that warmup boundary. Both must now
    # trade identically (the only legit edge is the later cross back below 30 at index 13).
    closes = [10, 9.5, 9.0, 8.5, 8.0, 7.5, 7.0, 8.0, 9.0, 10.0,
              11.0, 10.0, 9.0, 8.0, 7.0, 7.5, 8.0, 8.5, 9.0]
    bars = _flat_bars(closes)
    leaf = {"type": "rsi", "period": 3, "threshold": 30, "comparison": "below"}
    tautology = {"type": "rsi", "period": 3, "threshold": 90, "comparison": "below"}  # same warmup
    ex = {"type": "rsi", "period": 3, "threshold": 60, "comparison": "above"}
    rb = run_backtest(_strat(entry=leaf, exit=ex), bars)
    rw = run_backtest(_strat(entry={"type": "all_of", "conditions": [leaf, tautology]}, exit=ex), bars)
    assert rb.num_trades >= 1                                  # the drill is not vacuous
    assert [t.model_dump() for t in rb.trades] == [t.model_dump() for t in rw.trades]
    # and neither fired at the warmup boundary (first defined RSI bar is index 3 -> fill at t4)
    assert all(t.entry_time != "t4" for t in rb.trades + rw.trades)


def test_breakout_filter_no_longer_blocks_everything():
    # Fix: the breakout STATE compared close[i] to a window INCLUDING bar i (high[i] >= close[i]
    # almost always -> filter ~never true). With highs above closes the old form blocked this
    # entry; the prior-window form passes the bar the legacy breakout event fires on.
    closes = [1, 2, 1, 2, 3, 4, 5]
    bars = [_bar(f"t{i}", c, c + 0.5, c, c) for i, c in enumerate(closes)]
    res = run_backtest(_strat(filter={"type": "breakout", "lookback": 3, "direction": "high"}), bars)
    assert res.num_trades == 1
    assert res.trades[0].entry_time == "t5"        # the sma-cross entry the filter used to block


def test_backtest_trades_rsi2_mean_reversion_strategy():
    # gentle rise, sharp 3-bar dip (RSI(2) -> ~0, fires <10), sharp recovery (RSI(2) -> ~100, >70)
    closes = [100.0 + i for i in range(30)] + [124.0, 119.0, 114.0] + [120.0, 126.0, 132.0, 138.0]
    s = Strategy.model_validate({
        "name": "rsi2", "symbol": "SPY",
        "entry": {"type": "rsi", "period": 2, "threshold": 10.0, "comparison": "below"},
        "exit": {"type": "rsi", "period": 2, "threshold": 70.0, "comparison": "above"}})
    result = _run(s, closes)          # the file's existing helper for bars+run_backtest
    assert result.num_trades >= 1
    assert any(t.exit_reason == "signal" for t in result.trades)


def test_backtest_trades_each_new_close_based_leaf_type():
    dip = [100.0 + i for i in range(30)] + [124.0, 118.0, 112.0] + [118.0, 124.0, 130.0]
    for entry in ({"type": "consec_down", "count": 3},
                  {"type": "drop_from_high", "lookback": 10, "pct": 5.0},
                  {"type": "zscore", "period": 10, "threshold": -1.5, "comparison": "below"}):
        s = Strategy.model_validate({"name": "mr", "symbol": "SPY", "entry": entry,
                                     "take_profit_pct": 5.0, "stop_loss_pct": 8.0})
        result = _run(s, dip)
        assert result.num_trades >= 1, f"{entry['type']} produced no trades"


def test_backtest_trades_ibs_leaf():
    # ibs needs real bar RANGES: the three dip bars get a wide range with the close pinned at
    # the bottom (ibs = 1/7 ~= 0.14 < 0.25); normal bars close mid-range (ibs = 0.5).
    closes = [100.0 + i for i in range(30)] + [124.0, 118.0, 112.0] + [118.0, 124.0, 130.0]
    bars = [{"time": f"t{i:03d}", "open": c, "high": c + (6.0 if 30 <= i <= 32 else 1.0),
             "low": c - 1.0, "close": c, "volume": 1_000_000}
            for i, c in enumerate(closes)]
    s = Strategy.model_validate({"name": "ibs", "symbol": "SPY",
                                 "entry": {"type": "ibs", "level": 0.25, "side": "below"},
                                 "take_profit_pct": 5.0, "stop_loss_pct": 8.0})
    result = run_backtest(s, bars)      # match the file's actual run_backtest call convention
    assert result.num_trades >= 1
