import pytest
from webull_api.strategy.indicators import sma, ema, rsi, rolling_high, rolling_low, ibs, zscore


def test_sma_warmup_and_mean():
    assert sma([1, 2, 3, 4], 2) == [None, 1.5, 2.5, 3.5]
    assert sma([1, 2], 5) == [None, None]
    assert sma([1, None, 3], 2) == [None, None, None]


def test_ema_seeds_with_sma_then_recurses():
    out = ema([1, 2, 3, 4, 5], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(2.0)          # seed = SMA(1,2,3)
    assert out[3] == pytest.approx(2.0 + (4 - 2.0) * (2 / 4))   # 3.0
    assert out[4] == pytest.approx(3.0 + (5 - 3.0) * (2 / 4))   # 4.0


def test_rsi_all_gains_is_100_all_losses_is_0():
    up = rsi([1, 2, 3, 4, 5, 6], 3)
    assert up[-1] == pytest.approx(100.0)
    down = rsi([6, 5, 4, 3, 2, 1], 3)
    assert down[-1] == pytest.approx(0.0)
    assert rsi([1, 2], 5) == [None, None]


def test_rolling_high_low():
    assert rolling_high([1, 3, 2, 5, 4], 2) == [None, 3, 3, 5, 5]
    assert rolling_low([5, 3, 4, 1, 2], 2) == [None, 3, 3, 1, 1]


def test_ibs_basic_and_degenerate():
    highs, lows, closes = [10.0, 10.0, None, 10.0], [8.0, 8.0, 8.0, 8.0], [9.0, 8.0, 9.0, 10.0]
    out = ibs(highs, lows, closes)
    assert out[0] == 0.5 and out[1] == 0.0 and out[2] is None and out[3] == 1.0
    assert ibs([5.0], [5.0], [5.0]) == [None]      # high == low -> undefined


def test_zscore_warmup_gap_and_flat_window():
    vals = [1.0, 2.0, 3.0, None, 5.0, 5.0, 5.0]
    out = zscore(vals, 3)
    assert out[0] is None and out[1] is None                  # warmup
    assert out[2] is not None and abs(out[2] - 1.2247) < 1e-3  # (3-2)/popstd([1,2,3])
    assert out[3] is None and out[4] is None and out[5] is None  # gap inside the window
    assert out[6] is None                                     # flat window: stdev 0
    assert zscore([1.0, 2.0], 1) == [None, None]   # period <= 1 -> all None
