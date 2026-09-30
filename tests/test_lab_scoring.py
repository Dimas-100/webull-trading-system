import pytest
from webull_api.lab.scoring import consistency_score, normalized_drawdown, turnover_drag
from webull_api.lab.gate_a import stress_cost
from webull_api.lab.schema import (CostConfig, RegimeTag, ScoreWeights, WalkForwardReport,
                                   WindowResult)


def _wr(ret, bucket_vol):
    return WindowResult(fold_index=0, start_index=0, end_index=50,
                        regime=RegimeTag(trend="up", vol=bucket_vol), num_trades=10,
                        expectancy=1.0, avg_trade_return_pct=ret, profit_factor=1.5,
                        win_rate=55, return_pct=ret, max_drawdown_pct=-5, ulcer_index=2,
                        conditional_drawdown_95=-6, sharpe=1, sortino=1)


def _report(dsr=0.99, ci_lb=0.5, ddpct=-5.0):
    return WalkForwardReport(
        folds=[_wr(2.0, "low"), _wr(1.0, "high")], pooled_trades=20, pooled_expectancy=1.5,
        expectancy_ci=(ci_lb, 3.0), dsr=dsr, regime_buckets=["up/low", "up/high"],
        ulcer_index=2.0, conditional_drawdown_95=-6.0, max_drawdown_pct=ddpct, passed=True)


def test_normalized_drawdown_bounds_and_monotone():
    assert normalized_drawdown(0, 0, 0) == pytest.approx(0.0)
    worst = normalized_drawdown(100, -100, -100)
    assert 0.0 <= worst <= 1.0 and worst == pytest.approx(1.0)
    assert normalized_drawdown(10, -10, -10) > normalized_drawdown(2, -2, -2)


def test_turnover_drag_rises_with_trade_frequency():
    c = stress_cost(CostConfig(slippage_pct=0.05))
    assert turnover_drag(50, 100, cost=c) > turnover_drag(5, 100, cost=c)
    assert turnover_drag(0, 100, cost=c) == pytest.approx(0.0)


def test_consistency_score_is_pure_and_deterministic():
    r = _report()
    a = consistency_score(r, None, generations=0, m=1, proving=False)
    b = consistency_score(r, None, generations=0, m=1, proving=False)
    assert a.model_dump() == b.model_dump()
    assert 0.0 <= a.score <= 100.0


def test_consistency_score_capped_at_seventy_while_proving():
    r = _report(dsr=1.0, ci_lb=3.0, ddpct=-1.0)
    s = consistency_score(r, None, generations=0, m=1, proving=True)
    assert s.score <= ScoreWeights().proving_cap
    assert s.capped is True


def test_consistency_score_rewards_dsr_and_penalizes_generations():
    hi = consistency_score(_report(dsr=0.99), None, generations=0, m=1, proving=False)
    lo = consistency_score(_report(dsr=0.10), None, generations=0, m=1, proving=False)
    assert hi.score > lo.score
    few = consistency_score(_report(), None, generations=0, m=1, proving=False)
    many = consistency_score(_report(), None, generations=10, m=1, proving=False)
    assert few.score > many.score        # generations_survived is an overfit-risk penalty
