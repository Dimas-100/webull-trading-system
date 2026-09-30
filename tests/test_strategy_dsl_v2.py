import pytest
from pydantic import ValidationError
from webull_api.strategy.schema import (
    Strategy, AllOf, AnyOf, PriceVsSma, MacdSignal, AtrPct, leaf_count,
)


def _sma():
    return {"type": "sma_cross", "fast": 10, "slow": 20, "direction": "above"}


def _rsi():
    return {"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"}


def _pvs():
    return {"type": "price_vs_sma", "period": 200, "side": "above"}


def test_new_leaves_validate_and_bound():
    assert PriceVsSma(type="price_vs_sma", period=200, side="above").period == 200
    with pytest.raises(ValidationError):
        PriceVsSma(type="price_vs_sma", period=1, side="above")     # gt=1
    with pytest.raises(ValidationError):
        PriceVsSma(type="price_vs_sma", period=301, side="above")   # le=300
    assert AtrPct(type="atr_pct", period=14, level=3.0, side="below").level == 3.0
    with pytest.raises(ValidationError):
        AtrPct(type="atr_pct", level=101, side="above")             # level le=100


def test_macd_requires_fast_lt_slow():
    assert MacdSignal(type="macd", fast=12, slow=26, direction="above").ref == "signal"
    with pytest.raises(ValidationError):
        MacdSignal(type="macd", fast=26, slow=12, direction="above")


def test_composite_entry_with_two_leaves_ok():
    s = Strategy(name="x", symbol="AAPL",
                 entry={"type": "all_of", "conditions": [_sma(), _rsi()]})
    assert s.entry.type == "all_of" and len(s.entry.conditions) == 2


def test_rejects_nested_composite():
    # a composite cannot contain a composite (conditions are LEAVES only -> depth-1)
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL",
                 entry={"type": "all_of",
                        "conditions": [{"type": "all_of", "conditions": [_sma(), _rsi()]}, _pvs()]})


def test_rejects_four_leaf_composite():
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL",
                 entry={"type": "any_of", "conditions": [_sma(), _rsi(), _pvs(), {"type": "breakout", "lookback": 20, "direction": "high"}]})


def test_rejects_duplicate_leaves_in_composite():
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL",
                 entry={"type": "all_of", "conditions": [_sma(), _sma()]})


def test_rejects_over_six_total_leaves():
    three = {"type": "all_of", "conditions": [_sma(), _rsi(), _pvs()]}
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL", entry=three, exit=three, filter=_sma())  # 3+3+1 = 7


def test_six_total_leaves_is_allowed():
    three = {"type": "all_of", "conditions": [_sma(), _rsi(), _pvs()]}
    s = Strategy(name="x", symbol="AAPL", entry=three, exit=three)  # 3+3+0 = 6
    assert leaf_count(s.entry) + leaf_count(s.exit) + leaf_count(s.filter) == 6


def test_leaf_count_helper():
    assert leaf_count(None) == 0
    s = Strategy(name="x", symbol="AAPL", entry=_sma())
    assert leaf_count(s.entry) == 1
    s2 = Strategy(name="x", symbol="AAPL", entry={"type": "any_of", "conditions": [_sma(), _rsi()]})
    assert leaf_count(s2.entry) == 2


def test_filter_optional_default_none_and_origin_default_none():
    s = Strategy(name="x", symbol="AAPL", entry=_sma())
    assert s.filter is None and s.exit is None and s.origin is None


def test_back_compat_legacy_single_leaf_still_loads():
    s = Strategy(name="x", symbol="AAPL", entry=_sma())
    assert s.entry.type == "sma_cross" and s.timeframe == "1D"


# ── backtest dispatch / filter / cost (Task 4) ──
from webull_api.strategy.backtest import run_backtest
from webull_api.strategy.cost import CostModel


def _bar(t, c, h=None, l=None):
    return {"time": t, "open": c, "high": h if h is not None else c,
            "low": l if l is not None else c, "close": c, "volume": 1_000_000}


def _series(closes):
    return [_bar(f"t{i}", float(c)) for i, c in enumerate(closes)]


# a rise-then-fall path: sma_cross(2,3) up-crosses on the rise, down-crosses on the fall
_RISE_FALL = _series([1, 1, 1, 2, 3, 4, 5, 6, 7, 8, 7, 6, 5, 4, 3, 2, 1])


def _legacy_strat(**kw):
    base = dict(name="x", symbol="X",
                entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"})
    base.update(kw)
    return Strategy(**base)


def test_cost_none_is_a_noop_vs_default():
    s = _legacy_strat()
    a = run_backtest(s, _RISE_FALL)
    b = run_backtest(s, _RISE_FALL, cost=None)
    assert [t.model_dump() for t in a.trades] == [t.model_dump() for t in b.trades]
    assert a.total_return_pct == pytest.approx(b.total_return_pct)


def test_composite_entry_fires_through_dispatch():
    # AND of two leaves that both hold in the uptrend -> at least one trade taken
    s = Strategy(name="x", symbol="X",
                 entry={"type": "all_of",
                        "conditions": [{"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                                       {"type": "price_vs_sma", "period": 2, "side": "above"}]},
                 exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"})
    assert run_backtest(s, _RISE_FALL).num_trades >= 1


def test_filter_suppresses_entries():
    # an atr_pct>99% filter is held False everywhere -> no entry can ever arm
    blocked = _legacy_strat(filter={"type": "atr_pct", "period": 2, "level": 99, "side": "above"})
    assert run_backtest(_legacy_strat(), _RISE_FALL).num_trades >= 1
    assert run_backtest(blocked, _RISE_FALL).num_trades == 0


def test_filter_never_gates_the_exit():
    # filter is True on the rise (entry opens) and False on the fall, yet the exit must still fire
    s = _legacy_strat(filter={"type": "price_vs_sma", "period": 3, "side": "above"})
    r = run_backtest(s, _RISE_FALL)
    assert r.num_trades == 1
    assert r.trades[0].exit_reason == "signal"   # closed by the exit signal, not end_of_data


def test_cost_reduces_pnl_by_round_trip_commission():
    s = _legacy_strat()
    base = run_backtest(s, _RISE_FALL)
    costed = run_backtest(s, _RISE_FALL, cost=CostModel(commission_flat=5.0))
    assert base.num_trades == 1 and costed.num_trades == 1
    # entry/exit prices identical (no slippage); pnl drops by 2 * $5 commission
    assert costed.trades[0].entry_price == pytest.approx(base.trades[0].entry_price)
    assert costed.trades[0].pnl == pytest.approx(base.trades[0].pnl - 10.0)


# ── replay parity: strict_run == run_backtest for v2 strategies (Task 5) ──
from webull_api.strategy.replay import strict_run


def _composite_filter_strat():
    return Strategy(
        name="x", symbol="X",
        entry={"type": "all_of",
               "conditions": [{"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"},
                              {"type": "price_vs_sma", "period": 2, "side": "above"}]},
        exit={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "below"},
        filter={"type": "price_vs_sma", "period": 3, "side": "above"})


def test_strict_run_equals_run_backtest_for_composite_filter():
    s = _composite_filter_strat()
    a = strict_run(s, _RISE_FALL)
    b = run_backtest(s, _RISE_FALL)
    assert [t.model_dump() for t in a.trades] == [t.model_dump() for t in b.trades]
    assert a.total_return_pct == pytest.approx(b.total_return_pct)
    assert a.max_drawdown_pct == pytest.approx(b.max_drawdown_pct)


def test_strict_run_cost_matches_run_backtest_cost():
    s = _legacy_strat()
    c = CostModel(commission_flat=5.0, slippage_pct=0.1)
    a = strict_run(s, _RISE_FALL, cost=c)
    b = run_backtest(s, _RISE_FALL, cost=c)
    assert [t.model_dump() for t in a.trades] == [t.model_dump() for t in b.trades]
