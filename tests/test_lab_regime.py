from webull_api.lab.regime import compute_regime


def _bars(closes):
    return [{"time": f"t{i}", "open": c, "high": c, "low": c, "close": c, "volume": 0}
            for i, c in enumerate(closes)]


def test_uptrend_low_vol():
    r = compute_regime(_bars([100 + i * 0.1 for i in range(60)]), sma_period=20, vol_window=20)
    assert r.trend == "up"
    assert r.vol == "low"
    assert r.bucket() == "up/low"


def test_downtrend_low_vol():
    r = compute_regime(_bars([100 - i * 0.1 for i in range(60)]), sma_period=20, vol_window=20)
    assert r.trend == "down"


def test_high_vol_detected():
    # alternating +/-8% daily moves -> large annualized vol
    closes = [100.0]
    for i in range(40):
        closes.append(closes[-1] * (1.08 if i % 2 == 0 else 1 / 1.08))
    r = compute_regime(_bars(closes), sma_period=20, vol_window=20, high_vol_pct=25.0)
    assert r.vol == "high"


def test_degrades_on_short_slice():
    r = compute_regime(_bars([100.0]))
    assert r.trend == "side" and r.vol == "low"


# ---- Fix: short-slice trend detection (len < 2*sma_period) ----
# The old `half = min(p, len//2)` made sma_prev a DIFFERENT-period mean, so a perfect uptrend
# 1..100 classified "side" (and every ~90-130-bar Gate-A fold at sma_period=200 was garbled).
# The fix compares two same-length, non-overlapping block means (p_eff = min(period, len//2)).
def test_short_slice_perfect_uptrend_is_up():
    r = compute_regime(_bars([float(i) for i in range(1, 101)]))   # default sma_period=200 > len
    assert r.trend == "up"


def test_short_slice_perfect_downtrend_is_down():
    r = compute_regime(_bars([float(i) for i in range(100, 0, -1)]))
    assert r.trend == "down"


def test_short_slice_flat_is_side():
    r = compute_regime(_bars([100.0] * 50))
    assert r.trend == "side"
