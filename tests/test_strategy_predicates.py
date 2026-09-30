from webull_api.strategy import predicates
from webull_api.strategy.predicates import _held, _held3, _combined_held, _combined_held3, _edge
from webull_api.strategy.schema import (
    SmaCross, RsiCond, PriceVsSma, MacdSignal, AtrPct, AllOf, AnyOf, Breakout, Condition,
)


def test_edge_is_rising_edge():
    assert _edge([False, False, True, True, False, True]) == [False, False, True, False, False, True]
    assert _edge([True]) == [False]          # index 0 is always False
    assert _edge([]) == []


def test_combined_held_none_is_all_true():
    closes = [1.0, 2.0, 3.0]
    assert _combined_held(None, closes, closes, closes) == [True, True, True]


def test_held_price_vs_sma_above():
    closes = [10, 10, 10, 12, 8]          # sma2 then close-vs-sma2
    pvs = PriceVsSma(type="price_vs_sma", period=2, side="above")
    held = _held(pvs, closes, closes, closes)
    # idx0 sma undefined -> False; idx3 close 12 > sma2(11) True; idx4 close 8 < sma2(10) False
    assert held[0] is False
    assert held[3] is True
    assert held[4] is False


def test_held_sma_cross_is_a_state_not_a_crossing():
    closes = [1, 2, 3, 4, 5, 6]
    s = SmaCross(type="sma_cross", fast=2, slow=3, direction="above")
    held = _held(s, closes, closes, closes)
    # in a strict uptrend the 2-SMA stays above the 3-SMA: STATE stays True every defined bar
    assert held[-1] is True and held[-2] is True


def test_held_rsi_state():
    closes = [10, 9, 8, 7, 6, 5, 4, 3]     # falling -> low RSI
    r = RsiCond(type="rsi", period=3, threshold=30, comparison="below")
    held = _held(r, closes, closes, closes)
    assert held[-1] is True                 # RSI is below 30 in a steady decline


def test_held_macd_vs_zero():
    closes = [float(i) for i in range(1, 25)]   # strictly rising
    m = MacdSignal(type="macd", fast=3, slow=6, signal=2, ref="zero", direction="above")
    held = _held(m, closes, closes, closes)
    assert held[-1] is True                 # macd line > 0 in a sustained uptrend


def test_held_atr_pct():
    closes = [100.0] * 20
    highs = [101.0] * 20
    lows = [99.0] * 20
    a = AtrPct(type="atr_pct", period=14, level=0.5, side="above")
    held = _held(a, closes, highs, lows)
    assert held[-1] is True                 # ATR ~2 on a 100 price -> ~2% > 0.5%


def test_combined_held_and_or():
    closes = [1, 2, 3, 4, 5, 6]
    up = SmaCross(type="sma_cross", fast=2, slow=3, direction="above")
    down = SmaCross(type="sma_cross", fast=2, slow=3, direction="below")
    both = AllOf(type="all_of", conditions=[up, down])
    either = AnyOf(type="any_of", conditions=[up, down])
    assert _combined_held(both, closes, closes, closes)[-1] is False   # can't be both
    assert _combined_held(either, closes, closes, closes)[-1] is True  # up holds


def test_edge_requires_prior_state_defined_and_false():
    # Fix: an UNDEFINED (None) prior state is NOT False — the first defined-True bar of an
    # indicator (warmup boundary / post-gap reset) must not fire a phantom edge.
    assert _edge([None, True, True]) == [False, False, False]
    assert _edge([None, False, True]) == [False, False, True]
    assert _edge([False, None, True]) == [False, False, False]   # gap reset -> no edge either


def test_held3_is_none_during_warmup_and_held_projects_false():
    closes = [10, 9, 8, 7, 6, 5, 4, 3]
    r = RsiCond(type="rsi", period=3, threshold=30, comparison="below")
    h3 = _held3(r, closes, closes, closes)
    assert h3[0] is None and h3[1] is None and h3[2] is None      # RSI(3) undefined
    assert h3[-1] is True
    held = _held(r, closes, closes, closes)
    assert held[:3] == [False, False, False] and held[-1] is True # projection unchanged


def test_combined_held3_kleene_all_of_and_any_of():
    # child A (RSI-2) defined from idx2, child B (RSI-3) from idx3: all_of is None until BOTH
    # are defined (unless one is defined-False); any_of is True as soon as ONE is defined-True.
    short = RsiCond(type="rsi", period=2, threshold=90, comparison="below")   # defined idx2+
    long = RsiCond(type="rsi", period=3, threshold=90, comparison="below")    # defined idx3+
    closes = [10, 9, 8, 7, 6]
    both = AllOf(type="all_of", conditions=[short, long])
    either = AnyOf(type="any_of", conditions=[short, long])
    c_all = _combined_held3(both, closes, closes, closes)
    c_any = _combined_held3(either, closes, closes, closes)
    assert c_all[2] is None                        # long still undefined -> AND unknown
    assert c_all[3] is True
    assert c_any[2] is True                        # short already defined-True -> OR true
    assert _edge(c_all) == [False, False, False, False, False]    # no phantom at idx3


def test_held_breakout_state_uses_prior_window_extreme():
    # Fix: the state form compared close[i] to the rolling extreme INCLUDING bar i, where
    # high[i] >= close[i] almost always -> the state was ~never true. It must mirror the legacy
    # event's prior-window comparison (close[i] vs hi[i-1]).
    closes = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    highs = [c + 0.5 for c in closes]              # highs strictly above closes
    b = Breakout(type="breakout", lookback=3, direction="high")
    h3 = _held3(b, closes, highs, closes)
    # hi[i-1] over lookback 3 is defined from i=3: close 4.0 > hi[2]=3.5, 5.0 > hi[3]=4.5, ...
    assert h3[2] is None and h3[3] is True and h3[4] is True and h3[5] is True
    # every bar the LEGACY breakout event fires on must read state-True (breakout-as-filter works)
    from webull_api.strategy.backtest import _triggers
    trig = _triggers(b, closes, highs, closes)
    assert any(trig)
    assert all(h3[i] is True for i, fired in enumerate(trig) if fired)


from pydantic import TypeAdapter


def _nl(d):
    return TypeAdapter(Condition).validate_python(d)


def test_ibs_state_and_edge():
    highs = [10.0] * 5
    lows = [8.0] * 5
    closes = [9.0, 8.2, 8.2, 9.8, 8.1]     # ibs: .5, .1, .1, .9, .05
    held = predicates._held3(_nl({"type": "ibs", "level": 0.2, "side": "below"}),
                             closes, highs, lows)
    assert held == [False, True, True, False, True]
    assert predicates._edge(held) == [False, True, False, False, True]


def test_zscore_below_state_with_flat_warmup():
    closes = [10.0, 10.0, 10.0, 10.0, 7.0]
    held = predicates._held3(_nl({"type": "zscore", "period": 4, "threshold": -1.0,
                                  "comparison": "below"}), closes, closes, closes)
    assert held == [None, None, None, None, True]   # flat window -> None; the drop fires


def test_drop_from_high_uses_prior_bar_window():
    highs = [100.0, 101.0, 102.0, 102.0, 102.0]
    closes = [100.0, 101.0, 101.0, 96.0, 97.0]
    held = predicates._held3(_nl({"type": "drop_from_high", "lookback": 2, "pct": 5.0}),
                             closes, highs, closes)
    # rolling_high(highs,2) = [None,101,102,102,102]; threshold = hi[i-1]*0.95
    # i=2: 101 <= 101*0.95? no · i=3: 96 <= 102*0.95=96.9? yes · i=4: 97 <= 96.9? no
    assert held == [None, None, False, True, False]


def test_consec_down_run_gap_resets_to_undefined():
    closes = [5.0, 4.0, 3.0, 2.0, None, 3.0, 2.0, 1.0]
    held = predicates._held3(_nl({"type": "consec_down", "count": 3}), closes, closes, closes)
    assert held == [None, False, False, True, None, None, False, False]


def test_max_leaf_period_new_leaves():
    from webull_api.strategy import backtest
    assert backtest._max_leaf_period(_nl({"type": "zscore", "period": 20, "threshold": -2.0,
                                          "comparison": "below"})) == 20
    assert backtest._max_leaf_period(_nl({"type": "drop_from_high", "lookback": 50,
                                          "pct": 8.0})) == 50
    assert backtest._max_leaf_period(_nl({"type": "consec_down", "count": 4})) == 4
    assert backtest._max_leaf_period(_nl({"type": "ibs", "level": 0.2, "side": "below"})) == 1
