import pytest
from webull_api.analysis import technicals, support_resistance


def _bars(rows):  # rows: list of (high, low, close)
    return [{"time": f"t{i}", "open": c, "high": h, "low": l, "close": float(c)} for i, (h, l, c) in enumerate(rows)]


def test_technicals_uptrend_and_none_safe():
    closes = list(range(1, 61))            # strictly increasing 1..60
    bars = _bars([(c + 0.5, c - 0.5, c) for c in closes])
    t = technicals(bars)
    assert t["price"] == 60.0
    assert t["trend"] == "uptrend"          # price > sma20 > sma50
    assert t["rsi14"] == pytest.approx(100.0)
    assert t["pct_change_1"] == pytest.approx((60 / 59 - 1) * 100)
    short = technicals(bars[:3])
    assert short["sma50"] is None and short["trend"] == "sideways"


def test_support_resistance_finds_pivot_levels():
    highs = [101, 102, 103, 110, 103, 102, 101, 95, 96, 97, 98]
    lows = [99, 98, 97, 96, 95, 94, 93, 90, 95, 96, 97]
    bars = _bars([(highs[i], lows[i], 100) for i in range(11)])   # last close = 100
    sr = support_resistance(bars)
    assert sr["price"] == 100
    assert sr["resistance"] == [110]
    assert sr["support"] == [90]


def test_support_resistance_flat_data_is_empty():
    bars = _bars([(5, 5, 5) for _ in range(20)])
    sr = support_resistance(bars)
    assert sr["support"] == [] and sr["resistance"] == []
