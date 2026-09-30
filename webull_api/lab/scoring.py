"""ConsistencyScore: the SOLE ranking key, a pure function of the Gate-A/Gate-B reports (the LLM
can never set it). DD + cross-window consistency dominate raw return; anti-churn turnover penalty;
generations_survived is an overfit-risk FLAG (penalty), not a quality signal."""
from __future__ import annotations

from .gate_a import dsr, stress_cost
from .schema import (ConsistencyScore, DEFAULT_COST_CONFIG, DEFAULT_SCORE_WEIGHTS, ScoreWeights,
                     TrialBook, WalkForwardReport)
from webull_api.strategy.cost import CostModel

_DD_REF = 25.0   # a 25% drawdown reads as "fully bad" on the normalized scale


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def normalized_drawdown(ulcer: float, cdar: float, max_dd: float) -> float:
    """Blend Ulcer + CDaR + max-DD onto [0,1] (1 = worst). Monotone increasing in each."""
    u = min(abs(ulcer) / _DD_REF, 1.0)
    c = min(abs(cdar) / _DD_REF, 1.0)
    d = min(abs(max_dd) / _DD_REF, 1.0)
    return _clamp01(0.4 * u + 0.3 * c + 0.3 * d)


def turnover_drag(trades: int, bars: int, *, cost: CostModel) -> float:
    """Round-trip slippage cost per bar (anti-churn). Rises with trade frequency."""
    if bars <= 0 or trades <= 0:
        return 0.0
    round_trip_slip = 2 * cost.slippage_pct / 100.0
    return (trades / bars) * round_trip_slip


def _total_fold_bars(report: WalkForwardReport) -> int:
    return sum(f.end_index - f.start_index for f in report.folds)


def consistency_score(report: WalkForwardReport, book: TrialBook | None, *, generations: int,
                      weights: ScoreWeights = DEFAULT_SCORE_WEIGHTS, m: int,
                      proving: bool) -> ConsistencyScore:
    folds = report.folds
    prof_frac = (sum(1 for f in folds if f.return_pct > 0) / len(folds)) if folds else 0.0
    nd = normalized_drawdown(report.ulcer_index, report.conditional_drawdown_95,
                             report.max_drawdown_pct)
    ci_lb = report.expectancy_ci[0] if report.expectancy_ci else 0.0
    denom = abs(report.pooled_expectancy) if report.pooled_expectancy else 1.0
    ci_lb_term = _clamp01(ci_lb / denom)
    # trial-aware DSR: recompute over the live trial curve when Gate B is accruing, else Gate A's
    if book is not None and book.equity_curve:
        dsr_val = dsr(book.equity_curve, m=m)
    else:
        dsr_val = report.dsr
    regime_term = _clamp01(len(report.regime_buckets) / 2.0)
    drag = turnover_drag(report.pooled_trades, _total_fold_bars(report),
                         cost=stress_cost(DEFAULT_COST_CONFIG))

    raw01 = (weights.w1_profitable_window * prof_frac
             + weights.w2_low_drawdown * (1.0 - nd)
             + weights.w3_ci_lb_expectancy * ci_lb_term
             + weights.w4_dsr * dsr_val
             + weights.w5_regime_breadth * regime_term
             - weights.p1_turnover_drag * _clamp01(drag)
             - weights.p2_generations_flag * _clamp01(generations / 5.0))
    uncapped = max(0.0, min(100.0, raw01 * 100.0))
    capped = proving and uncapped >= weights.proving_cap
    score = min(uncapped, weights.proving_cap) if proving else uncapped

    return ConsistencyScore(
        score=score, profitable_window_frac=prof_frac, normalized_drawdown=nd,
        ci_lb_expectancy=ci_lb, dsr=dsr_val, regime_breadth_frac=regime_term,
        turnover_drag=drag, generations_survived=generations, capped=capped)
