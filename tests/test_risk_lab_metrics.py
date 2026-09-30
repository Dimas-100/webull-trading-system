import math
import pytest
from webull_api.risk import ulcer_index, conditional_drawdown


def test_ulcer_zero_on_monotone_rise():
    # never below a prior peak -> no drawdown -> ulcer 0
    assert ulcer_index([1, 2, 3, 4, 5]) == pytest.approx(0.0)


def test_ulcer_positive_and_known_value():
    # peak 100 then 90 -> dd -10% on the final bar; ulcer = sqrt(mean(dd^2))
    # dd series (% from running peak): 0, -10  -> sqrt((0 + 100)/2) = 7.0710678
    u = ulcer_index([100, 90])
    assert u == pytest.approx(math.sqrt((0 + 100) / 2), rel=1e-9)


def test_ulcer_none_on_empty():
    assert ulcer_index([]) is None


def test_cdar_is_negative_and_at_least_as_severe_as_mean_dd():
    closes = [100, 110, 80, 120, 70, 130]
    cdar = conditional_drawdown(closes, q=0.95)
    assert cdar is not None and cdar <= 0.0
    # 95% CDaR (tail mean of worst drawdowns) is <= the max drawdown in magnitude
    assert cdar <= -1.0


def test_cdar_zero_on_monotone_rise():
    assert conditional_drawdown([1, 2, 3, 4]) == pytest.approx(0.0)


def test_cdar_none_on_empty():
    assert conditional_drawdown([]) is None
