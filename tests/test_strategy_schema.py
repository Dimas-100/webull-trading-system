import pytest
from pydantic import ValidationError
from webull_api.strategy.schema import Strategy


def _entry():
    return {"type": "sma_cross", "fast": 20, "slow": 50, "direction": "above"}


def test_minimal_strategy_defaults():
    s = Strategy(name="x", symbol="AAPL", entry=_entry())
    assert s.timeframe == "1D"
    assert s.lookback_bars == 500
    assert s.sizing.type == "pct_equity" and s.sizing.value == 100
    assert s.starting_equity == 10000
    assert s.exit is None


def test_entry_discriminated_by_type():
    s = Strategy(name="x", symbol="AAPL",
                 entry={"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"})
    assert s.entry.type == "rsi" and s.entry.period == 14


def test_fast_must_be_less_than_slow():
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL",
                 entry={"type": "sma_cross", "fast": 50, "slow": 20, "direction": "above"})


def test_rejects_unknown_condition_and_bad_values():
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL", entry={"type": "macd"})
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL", entry={"type": "rsi", "period": 0, "threshold": 30, "comparison": "below"})
    with pytest.raises(ValidationError):
        Strategy(name="x", symbol="AAPL", entry=_entry(), sizing={"type": "pct_equity", "value": 0})


def test_new_mean_reversion_leaves_validate_and_roundtrip():
    s = Strategy.model_validate({
        "name": "mr", "symbol": "SPY",
        "entry": {"type": "any_of", "conditions": [
            {"type": "ibs", "level": 0.2, "side": "below"},
            {"type": "consec_down", "count": 3}]},
        "filter": {"type": "price_vs_sma", "period": 200, "side": "above"},
        "exit": {"type": "zscore", "period": 20, "threshold": 0.0, "comparison": "above"}})
    assert Strategy.model_validate(s.model_dump()).model_dump() == s.model_dump()
    s2 = Strategy.model_validate({"name": "dip", "symbol": "SPY",
                                  "entry": {"type": "drop_from_high", "lookback": 20, "pct": 5.0}})
    assert s2.entry.type == "drop_from_high"


def test_new_leaf_field_bounds_rejected():
    for bad in ({"type": "ibs", "level": 0.0, "side": "below"},
                {"type": "ibs", "level": 1.0, "side": "below"},
                {"type": "consec_down", "count": 1},
                {"type": "consec_down", "count": 11},
                {"type": "zscore", "period": 1, "threshold": -2.0, "comparison": "below"},
                {"type": "drop_from_high", "lookback": 1, "pct": 5.0},
                {"type": "drop_from_high", "lookback": 20, "pct": 0.0}):
        with pytest.raises(ValidationError):
            Strategy.model_validate({"name": "x", "symbol": "SPY", "entry": bad})
