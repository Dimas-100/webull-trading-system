import pytest
from webull_api.strategy.cost import (
    CostModel, NO_COST, entry_fill, exit_fill, stop_fill, commission, liquidity_ok,
)


def test_no_cost_is_zero_cost():
    assert NO_COST == CostModel()
    assert entry_fill(100.0, NO_COST) == 100.0
    assert exit_fill(100.0, NO_COST) == 100.0
    assert commission(10.0, NO_COST) == 0.0


def test_entry_and_exit_apply_per_side_slippage():
    c = CostModel(slippage_pct=0.5)
    assert entry_fill(100.0, c) == pytest.approx(100.5)   # buy pays up
    assert exit_fill(100.0, c) == pytest.approx(99.5)     # sell receives less


def test_stop_fill_gaps_through_on_a_down_gap():
    c = CostModel(slippage_pct=0.0, stop_slippage_pct=0.0)
    # no gap: open above the stop -> fill at the stop
    assert stop_fill(95.0, 98.0, c) == pytest.approx(95.0)
    # gap down through the stop: open below the stop -> fill at the worse open
    assert stop_fill(95.0, 90.0, c) == pytest.approx(90.0)


def test_stop_fill_adds_slippage_and_stop_slippage():
    c = CostModel(slippage_pct=0.1, stop_slippage_pct=0.2)
    # base = min(95, 98) = 95; *(1 - (0.1+0.2)/100)
    assert stop_fill(95.0, 98.0, c) == pytest.approx(95.0 * (1 - 0.003))


def test_commission_floor_and_per_share():
    c = CostModel(commission_flat=1.0, commission_per_share=0.01, min_commission=5.0)
    assert commission(100.0, c) == pytest.approx(5.0)     # 1 + 1.0 = 2 -> floored to 5
    assert commission(1000.0, c) == pytest.approx(11.0)   # 1 + 10 = 11 > floor


def test_liquidity_ok_cap():
    assert liquidity_ok(1e9, None, 10.0, CostModel(liquidity_adv_frac=0.01)) is True   # no volume known
    assert liquidity_ok(1e9, 1000.0, 10.0, CostModel(liquidity_adv_frac=0.0)) is True  # cap disabled
    c = CostModel(liquidity_adv_frac=0.01)
    # cap = 0.01 * 1_000_000 * 10 = 100_000
    assert liquidity_ok(50_000.0, 1_000_000.0, 10.0, c) is True
    assert liquidity_ok(200_000.0, 1_000_000.0, 10.0, c) is False
