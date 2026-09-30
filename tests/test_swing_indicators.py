from webull_api.strategy.indicators import atr, macd


def test_atr_constant_range_converges():
    # H-L = 2 every bar, no gaps -> TR=2 -> ATR=2 after warmup
    highs = [11.0] * 20
    lows = [9.0] * 20
    closes = [10.0] * 20
    out = atr(highs, lows, closes, 14)
    assert out[:14] == [None] * 14  # warmup (needs prev close + 14 TRs)
    assert out[-1] is not None and abs(out[-1] - 2.0) < 1e-9


def test_atr_short_series_all_none():
    assert atr([1.0, 2.0], [0.5, 1.0], [1.0, 1.5], 14) == [None, None]


def test_macd_shapes_and_zero_on_flat():
    values = [10.0] * 60
    m = macd(values)
    assert set(m) == {"macd", "signal", "hist"}
    assert len(m["macd"]) == len(values) == len(m["hist"]) == len(m["signal"])
    assert abs(m["hist"][-1]) < 1e-9  # flat series -> zero histogram


def test_macd_positive_hist_on_rising_series():
    values = [float(i) for i in range(60)]  # steadily rising
    m = macd(values)
    assert m["macd"][-1] is not None and m["macd"][-1] > 0
