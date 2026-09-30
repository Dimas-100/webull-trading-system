from webull_api.lab.archetype import classify
from webull_api.strategy.schema import (AllOf, Breakout, MacdSignal, PriceVsSma, RsiCond,
                                        SmaCross, Strategy)


def _s(entry, **kw):
    return Strategy(name="s", symbol="AAPL", entry=entry, **kw)


def test_sma_cross_is_trend_follow():
    assert classify(_s(SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))) == "trend_follow"


def test_price_vs_sma_is_trend_follow():
    assert classify(_s(PriceVsSma(type="price_vs_sma", period=50, side="above"))) == "trend_follow"


def test_breakout_is_breakout():
    assert classify(_s(Breakout(type="breakout", lookback=20, direction="high"))) == "breakout"


def test_rsi_below_is_mean_revert():
    assert classify(_s(RsiCond(type="rsi", period=14, threshold=30, comparison="below"))) == "mean_revert"


def test_rsi_above_is_momentum():
    assert classify(_s(RsiCond(type="rsi", period=14, threshold=70, comparison="above"))) == "momentum"


def test_macd_is_momentum():
    assert classify(_s(MacdSignal(type="macd", fast=12, slow=26, signal=9,
                                  ref="signal", direction="above"))) == "momentum"


def test_filter_present_is_filtered_trigger():
    s = _s(Breakout(type="breakout", lookback=20, direction="high"),
           filter=PriceVsSma(type="price_vs_sma", period=200, side="above"))
    assert classify(s) == "filtered_trigger"


def test_composite_uses_most_trigger_like_leaf():
    entry = AllOf(type="all_of", conditions=[
        SmaCross(type="sma_cross", fast=10, slow=20, direction="above"),
        Breakout(type="breakout", lookback=20, direction="high"),
    ])
    assert classify(_s(entry)) == "breakout"


def test_new_leaf_archetypes():
    def mk(entry):
        return Strategy.model_validate({"name": "x", "symbol": "SPY", "entry": entry})
    assert classify(mk({"type": "ibs", "level": 0.2, "side": "below"})) == "mean_revert"
    assert classify(mk({"type": "ibs", "level": 0.8, "side": "above"})) == "momentum"
    assert classify(mk({"type": "zscore", "period": 20, "threshold": -2.0,
                        "comparison": "below"})) == "mean_revert"
    assert classify(mk({"type": "zscore", "period": 20, "threshold": 2.0,
                        "comparison": "above"})) == "momentum"
    assert classify(mk({"type": "drop_from_high", "lookback": 20, "pct": 5.0})) == "mean_revert"
    assert classify(mk({"type": "consec_down", "count": 3})) == "mean_revert"
