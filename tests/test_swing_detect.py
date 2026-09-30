from webull_api.swing.detect import reversal_trigger, pullback_swing_low


def _b(o, h, l, c, v=1e6, t="d"):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v}


def test_close_above_prior_high():
    bars = [_b(10, 10.4, 9, 9.5), _b(9.6, 10.6, 9.4, 10.5)]  # close 10.5 > prior high 10.4
    t = reversal_trigger(bars)
    assert t and t["kind"] == "close_above_prior_high" and abs(t["trigger_high"] - 10.6) < 1e-9


def test_engulfing():
    # prior red body 10->9.5 (high 10.5); current green engulfs it, closes upper half, NOT above prior high
    bars = [_b(10.0, 10.5, 9.4, 9.5), _b(9.4, 10.4, 9.3, 10.2)]
    t = reversal_trigger(bars)
    assert t and t["kind"] == "engulfing" and abs(t["trigger_high"] - 10.4) < 1e-9


def test_hammer():
    # long lower wick, small body near top, closes upper half; not above prior high, not engulfing
    bars = [_b(10.5, 11, 10, 10.8), _b(10.0, 10.3, 8.5, 10.2)]
    t = reversal_trigger(bars)
    assert t and t["kind"] == "hammer"


def test_no_trigger_on_down_candle():
    bars = [_b(10, 11, 9, 10), _b(10, 10.2, 9.0, 9.1)]
    assert reversal_trigger(bars) is None


def test_pullback_low_window():
    bars = [_b(10, 11, 8, 10) for _ in range(5)] + [_b(10, 11, 7.5, 10), _b(10, 11, 9, 10.5)]
    assert abs(pullback_swing_low(bars, window=10) - 7.5) < 1e-9
