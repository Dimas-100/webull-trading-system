import pytest
from webull_api.strategy.schema import Strategy
from webull_api.strategy.backtest import run_backtest
from webull_api.strategy.replay import (
    start_live, append_live_bars, strict_run, step_to_decision, apply_decision, finalize,
)


def _bar(t, c):
    return {"time": t, "open": c, "high": c, "low": c, "close": c, "volume": 0}


def _bars(seq):
    return [_bar(f"t{i}", c) for i, c in enumerate(seq)]


def _strat(**kw):
    base = dict(name="x", symbol="X",
                entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"},
                sizing={"type": "pct_equity", "value": 100}, starting_equity=10000)
    base.update(kw)
    return Strategy(**base)


def test_start_live_seeds_and_starts_now():
    seed = _bars([1, 2, 3, 2])
    s = start_live(_strat(), seed)
    assert s.mode == "live"
    assert s.cursor == 4 and s.live_start_index == 4
    # no live bars yet: stepping with mark_finished_on_end=False must NOT finish
    s, dp = step_to_decision(s, mark_finished_on_end=False)
    assert dp is None and s.status == "in_progress"


def test_append_live_bars_confirmed_and_newer_only():
    s = start_live(_strat(), _bars([1, 2]))         # window: t0,t1
    fresh = _bars([1, 2, 3, 4])                       # t0..t3; t3 is the forming bar
    assert append_live_bars(s, fresh) == 1           # appends t2 only (t3 held back)
    assert [b["time"] for b in s.window_bars] == ["t0", "t1", "t2"]
    assert append_live_bars(s, fresh) == 0           # idempotent


def test_strict_run_equals_run_backtest_at_zero():
    bars = _bars([1, 2, 1, 2, 3, 4, 5, 4, 3, 4, 5, 6, 5, 4, 5, 6, 7])
    strat = _strat()
    assert strict_run(strat, bars, 0).model_dump() == run_backtest(strat, bars).model_dump()


def test_live_drill_surfaces_decision_and_trades():
    seed = _bars([5, 5, 5])                           # flat warmup, no signal
    s = start_live(_strat(), seed)
    s, dp = step_to_decision(s, mark_finished_on_end=False)
    assert dp is None                                 # nothing yet
    # live bars: dip then rise -> an up-cross; last bar (t6) held back as forming
    fresh = _bars([5, 5, 5, 3, 4, 6, 7])
    assert append_live_bars(s, fresh) >= 1
    s, dp = step_to_decision(s, mark_finished_on_end=False)
    assert dp is not None                             # an entry signal surfaced in the live region
    s = apply_decision(s, True)
    res = finalize(s)
    assert res.taken == 1 and res.skipped == 0
    assert res.user.num_trades >= 1
    assert res.user.equity_curve                      # live-period equity points exist
