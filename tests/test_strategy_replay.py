import pytest
from webull_api.strategy.schema import Strategy
from webull_api.strategy.backtest import run_backtest
from webull_api.strategy.replay import (
    start_replay, step_to_decision, apply_decision, finalize, pick_window, explain_signal,
)
from webull_api.journal.schema import ThesisRecord


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 0}


def _flat(closes):
    return [_bar(f"t{i}", c, c, c, c) for i, c in enumerate(closes)]


def _strat(**kw):
    base = dict(name="x", symbol="X",
                entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"},
                sizing={"type": "pct_equity", "value": 100}, starting_equity=10000)
    base.update(kw)
    return Strategy(**base)


def test_pauses_at_first_entry_while_flat():
    state = start_replay(_strat(), _flat([1, 2, 1, 2, 3, 4, 5]))
    state, dp = step_to_decision(state)
    assert dp is not None
    assert dp.bar_index == 4          # 2/3 SMA cross fires at bar 4 (close)
    assert "SMA" in dp.why


def test_take_fills_next_open_and_records_decision():
    state = start_replay(_strat(), _flat([1, 2, 1, 2, 3, 4, 5]))
    state, dp = step_to_decision(state)
    state = apply_decision(state, True)
    res = finalize(state)
    assert res.taken == 1 and res.skipped == 0
    assert res.user.num_trades == 1
    assert res.user.trades[0].entry_time == "t5" and res.user.trades[0].entry_price == pytest.approx(4.0)


def test_skip_takes_no_trade_but_benchmark_does():
    state = start_replay(_strat(), _flat([1, 2, 1, 2, 3, 4, 5]))
    state, dp = step_to_decision(state)
    state = apply_decision(state, False)
    res = finalize(state)
    assert res.taken == 0 and res.skipped == 1
    assert res.user.num_trades == 0
    assert res.benchmark.num_trades == 1          # the strict run still took it


def test_take_everything_equals_run_backtest():
    closes = [1, 2, 1, 2, 3, 4, 5, 4, 3, 4, 5, 6, 5, 4, 5, 6, 7, 6, 5, 6, 7]
    bars = _flat(closes)
    strat = _strat()
    state = start_replay(strat, bars)
    while True:
        state, dp = step_to_decision(state)
        if dp is None:
            break
        state = apply_decision(state, True)
    res = finalize(state)
    bench = run_backtest(strat, bars)
    assert [t.model_dump() for t in res.user.trades] == [t.model_dump() for t in bench.trades]
    assert res.user.total_return_pct == pytest.approx(bench.total_return_pct)


def test_pick_window_respects_warmup_and_length_and_is_seedable():
    bars = _flat(list(range(100)))
    w1 = pick_window(bars, length=10, warmup=5, seed=42)
    w2 = pick_window(bars, length=10, warmup=5, seed=42)
    assert len(w1) == 15 and w1 == w2          # deterministic when seeded
    with pytest.raises(ValueError):
        pick_window(bars, length=200, warmup=5)


def test_explain_signal_covers_types():
    assert "SMA" in explain_signal(_strat().entry)
    rsi = Strategy(name="x", symbol="X", entry={"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"}).entry
    assert "RSI" in explain_signal(rsi)


def test_apply_decision_carries_thesis():
    """apply_decision with a ThesisRecord stores it on the Decision."""
    state = start_replay(_strat(), _flat([1, 2, 1, 2, 3, 4, 5]))
    state, dp = step_to_decision(state)
    assert dp is not None
    thesis = ThesisRecord(confidence=3, setup="breakout")
    state = apply_decision(state, True, thesis)
    assert state.decisions[-1].thesis is not None
    assert state.decisions[-1].thesis.confidence == 3
    assert state.decisions[-1].thesis.setup == "breakout"


def test_apply_decision_without_thesis_is_none():
    """apply_decision without thesis → Decision.thesis is None."""
    state = start_replay(_strat(), _flat([1, 2, 1, 2, 3, 4, 5]))
    state, dp = step_to_decision(state)
    assert dp is not None
    state = apply_decision(state, False)
    assert state.decisions[-1].thesis is None


def test_take_everything_equals_run_backtest_still_passes():
    """Confirm the equivalence invariant holds after adding thesis param (regression guard)."""
    closes = [1, 2, 1, 2, 3, 4, 5, 4, 3, 4, 5, 6, 5, 4, 5, 6, 7, 6, 5, 6, 7]
    bars = _flat(closes)
    strat = _strat()
    state = start_replay(strat, bars)
    while True:
        state, dp = step_to_decision(state)
        if dp is None:
            break
        state = apply_decision(state, True)  # no thesis — default None, keeps strict_run compat
    res = finalize(state)
    bench = run_backtest(strat, bars)
    assert [t.model_dump() for t in res.user.trades] == [t.model_dump() for t in bench.trades]
    assert res.user.total_return_pct == pytest.approx(bench.total_return_pct)


def test_finalize_tolerates_none_final_close():
    # Fix: a None final close (degraded bar) used to raise in the end-of-data close/settle paths.
    bars = _flat([1, 2, 1, 2, 3, 4, 5]) + [_bar("t7", None, None, None, None)]
    state = start_replay(_strat(exit=None), bars)
    state, dp = step_to_decision(state)
    state = apply_decision(state, True)
    res = finalize(state)                          # must not raise
    assert res.user.num_trades == 1
    assert res.user.trades[-1].exit_reason == "end_of_data"
    assert res.user.trades[-1].exit_price == pytest.approx(5.0)   # last non-None close
