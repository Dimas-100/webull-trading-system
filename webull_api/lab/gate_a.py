"""Gate A: walk-forward / segmented out-of-sample overfit filter. Pure. The lab-global trial
count `m` is passed in (the store owns the monotone counter). Trial fills route ONLY through the
reused strategy.replay engine + the cost model — this module never imports webull_api.trading."""
from __future__ import annotations

import copy
import math
import random
import statistics
from typing import Literal

from pydantic import ValidationError

from .schema import CostConfig, DEFAULT_COST_CONFIG, Fold, GateAConfig, DEFAULT_GATE_A_CONFIG, LockboxResult, WindowResult, WalkForwardReport, _INT_FIELDS
from .regime import compute_regime
from webull_api import risk
from webull_api.strategy import replay
from webull_api.strategy.cost import CostModel, NO_COST
from webull_api.strategy.schema import EquityPoint, Strategy, leaf_count


class NotEnoughData(Exception):
    """Raised by make_folds when fewer than 3 OOS folds are formable over the lookback."""


def stress_cost(cfg: CostConfig = DEFAULT_COST_CONFIG) -> CostModel:
    """STRESS_COST: double the assumed slippage with a 0.10% floor; same instance threaded into
    Gate A and Gate B so the verdict is internally consistent."""
    slip = max(2 * cfg.slippage_pct, 0.10)
    return CostModel(
        slippage_pct=slip,
        stop_slippage_pct=cfg.stop_slippage_pct,
        commission_flat=cfg.commission_flat,
        commission_per_share=cfg.commission_per_share,
        min_commission=cfg.min_commission,
        liquidity_adv_frac=cfg.liquidity_adv_frac,
    )


def make_folds(n_bars: int, *, k: int, warmup: int, min_fold_bars: int, embargo: int) -> list[Fold]:
    """K contiguous, non-overlapping test segments over bars[warmup:n). Auto-reduce k so each
    fold holds >= min_fold_bars; raise NotEnoughData if <3 folds formable. Guarantees a
    no-look-ahead split (out_sample.lo > in_sample.hi). `embargo` (the per-strategy warmup) is
    applied to metric attribution in fold_metrics, not to the partition boundaries."""
    usable = n_bars - warmup
    if usable <= 0:
        raise NotEnoughData(f"warmup {warmup} >= n_bars {n_bars}")
    max_k = usable // min_fold_bars
    k_eff = min(k, max_k)
    if k_eff < 3:
        raise NotEnoughData(
            f"only {usable} usable bars; cannot form 3 folds of >= {min_fold_bars}")
    seg = usable // k_eff
    folds: list[Fold] = []
    for i in range(k_eff):
        start = warmup + i * seg
        end = warmup + (i + 1) * seg if i < k_eff - 1 else n_bars
        folds.append(Fold(index=i, start=start, end=end))
    return folds


_PF_NO_LOSS = 999.0   # sentinel profit factor when there are wins but no losses


def _leaf_periods(node) -> list[int]:
    """Indicator periods used by a leaf or composite (for warmup sizing)."""
    if node is None:
        return []
    t = getattr(node, "type", None)
    if t in ("all_of", "any_of"):
        out: list[int] = []
        for c in node.conditions:
            out += _leaf_periods(c)
        return out
    if t in ("sma_cross", "ema_cross"):
        return [node.slow]
    if t == "rsi":
        return [node.period]
    if t == "breakout":
        return [node.lookback]
    if t == "price_vs_sma":
        return [node.period]
    if t == "macd":
        return [node.slow, node.signal]
    if t == "atr_pct":
        return [node.period]
    if t == "zscore":
        return [node.period]
    if t == "drop_from_high":
        return [node.lookback]
    if t == "consec_down":
        return [node.count]
    if t == "ibs":
        return [1]
    return []


def _warmup_bars(strategy, cfg: GateAConfig = DEFAULT_GATE_A_CONFIG) -> int:
    periods = (_leaf_periods(strategy.entry) + _leaf_periods(strategy.exit)
               + _leaf_periods(strategy.filter))
    return (max(periods) if periods else 1) + cfg.warmup_buffer


def fold_metrics(strategy, bars, fold, *, warmup: int, cost: CostModel) -> WindowResult:
    """Net-of-cost OOS metrics for one fold. strict_run over bars[fold.start-warmup : fold.end]
    with start_index=warmup so the first `warmup` bars feed indicators only (embargo) and trades
    are attributed to the fold they ENTER. Boundary-straddling trades (exit_reason 'end_of_data')
    are excluded from trade stats (carried MTM in the curve) so a synthetic exit never drives the
    pass/fail."""
    lo = max(0, fold.start - warmup)
    sl = bars[lo:fold.end]
    local_start = fold.start - lo
    res = replay.strict_run(strategy, sl, start_index=local_start, cost=cost)

    attributed = [t for t in res.trades if t.exit_reason != "end_of_data"]
    wins = [t for t in attributed if t.pnl > 0]
    losses = [t for t in attributed if t.pnl <= 0]
    sum_loss = sum(t.pnl for t in losses)
    if sum_loss < 0:
        pf = sum(t.pnl for t in wins) / abs(sum_loss)
    elif wins:
        pf = _PF_NO_LOSS
    else:
        pf = 0.0
    eq_closes = [p.equity for p in res.equity_curve]
    regime = compute_regime(bars[fold.start:fold.end])
    return WindowResult(
        fold_index=fold.index, start_index=fold.start, end_index=fold.end, regime=regime,
        num_trades=len(attributed),
        expectancy=(sum(t.pnl for t in attributed) / len(attributed)) if attributed else 0.0,
        avg_trade_return_pct=(sum(t.return_pct for t in attributed) / len(attributed))
                             if attributed else 0.0,
        profit_factor=pf,
        win_rate=(len(wins) / len(attributed) * 100) if attributed else 0.0,
        return_pct=res.total_return_pct,
        max_drawdown_pct=res.max_drawdown_pct,
        ulcer_index=risk.ulcer_index(eq_closes) or 0.0,
        conditional_drawdown_95=risk.conditional_drawdown(eq_closes) or 0.0,
        sharpe=risk.sharpe(eq_closes) or 0.0,
        sortino=risk.sortino(eq_closes) or 0.0,
    )


def pooled_expectancy(folds: list[WindowResult]) -> float:
    """Trade-weighted mean of WindowResult.expectancy — DOLLARS per trade (feeds per_symbol_edge,
    where only the SIGN is consumed by breadth). NOT the report-level pooled_expectancy, which is
    in PERCENT (mean trade return_pct)."""
    n = sum(f.num_trades for f in folds)
    if n == 0:
        return 0.0
    return sum(f.expectancy * f.num_trades for f in folds) / n


def regime_coverage_ok(folds: list[WindowResult], *, min_buckets: int,
                        require_high_vol: bool) -> bool:
    buckets = {f.regime.bucket() for f in folds}
    if len(buckets) < min_buckets:
        return False
    if require_high_vol and not any(f.regime.vol == "high" for f in folds):
        return False
    return True


_Z_TWO_SIDED = {0.10: 1.6448536269, 0.05: 1.9599639845, 0.01: 2.5758293035}


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _to_returns(returns_or_curve) -> list[float]:
    if not returns_or_curve:
        return []
    first = returns_or_curve[0]
    if isinstance(first, (int, float)):
        return [float(x) for x in returns_or_curve]
    eq = [p.equity for p in returns_or_curve]      # list[EquityPoint]
    return [eq[i] / eq[i - 1] - 1.0 for i in range(1, len(eq)) if eq[i - 1]]


def _skew(r: list[float]) -> float:
    n, m, sd = len(r), statistics.mean(r), statistics.pstdev(r)
    if n < 3 or sd == 0:
        return 0.0
    return sum(((x - m) / sd) ** 3 for x in r) / n


def _kurt(r: list[float]) -> float:
    n, m, sd = len(r), statistics.mean(r), statistics.pstdev(r)
    if n < 4 or sd == 0:
        return 3.0
    return sum(((x - m) / sd) ** 4 for x in r) / n


def expectancy_ci(trade_returns, *, alpha: float = 0.05,
                  method: Literal["bootstrap", "t"] = "bootstrap",
                  seed: int = 0) -> tuple[float, float]:
    """Lower/upper CI bound on the mean trade P&L. Deterministic (seeded bootstrap)."""
    vals = [float(v) for v in trade_returns]
    if not vals:
        return (0.0, 0.0)
    if len(vals) == 1:
        return (vals[0], vals[0])
    if method == "t":
        mean = statistics.mean(vals)
        se = statistics.stdev(vals) / math.sqrt(len(vals))
        z = _Z_TWO_SIDED.get(round(alpha, 2), 1.9599639845)
        return (mean - z * se, mean + z * se)
    rng = random.Random(seed)
    n, b = len(vals), 1000
    means = sorted(statistics.mean(vals[rng.randrange(n)] for _ in range(n)) for _ in range(b))
    lo = means[max(0, int((alpha / 2) * b))]
    hi = means[min(b - 1, int((1 - alpha / 2) * b))]
    return (lo, hi)


_EULER_GAMMA = 0.5772156649015329


def _expected_max_sharpe(m: int) -> float:
    """E[max of m iid N(0,1)] — the Bailey & Lopez de Prado expression
    (1-γ)·Φ⁻¹(1-1/m) + γ·Φ⁻¹(1-1/(m·e)), γ = Euler-Mascheroni; 0 for m <= 1.
    (The old sqrt(2·ln m) leading-order bound over-deflated ~37% at m=10.)"""
    if m <= 1:
        return 0.0
    nd = statistics.NormalDist()
    return ((1.0 - _EULER_GAMMA) * nd.inv_cdf(1.0 - 1.0 / m)
            + _EULER_GAMMA * nd.inv_cdf(1.0 - 1.0 / (m * math.e)))


def dsr(returns_or_curve, *, m: int, ppy: int = 252, sr_benchmark: float = 0.0,
        skew: float | None = None, kurt: float | None = None) -> float:
    """Deflated Sharpe Ratio (Bailey & Lopez de Prado): probability the Sharpe exceeds
    sr_benchmark given `m` trials and the sample length. Stdlib only (no scipy). Monotone
    decreasing in m, increasing in n. NOTE: `ppy` is accepted for call-site symmetry with the
    configs but does not enter the formula — the DSR compares the PER-BAR Sharpe to a per-bar
    benchmark, and the resulting probability is invariant to annualization."""
    r = _to_returns(returns_or_curve)
    n = len(r)
    if n < 3:
        return 0.0
    mean = statistics.mean(r)
    sd = statistics.pstdev(r)
    if sd == 0:
        return 1.0 if mean > 0 else 0.0
    sr = mean / sd
    sk = skew if skew is not None else _skew(r)
    ku = kurt if kurt is not None else _kurt(r)
    var_term = 1.0 - sk * sr + (ku - 1.0) / 4.0 * sr * sr
    if var_term <= 0:
        var_term = 1e-9
    expected_max = _expected_max_sharpe(m)
    sr0 = sr_benchmark + math.sqrt(var_term / (n - 1)) * expected_max
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(var_term)
    return _norm_cdf(z)


def deflated_edge_floor(base: float, m: int, n: int, *, k_defl: float) -> float:
    """Cheap secondary net-edge screen kept alongside DSR."""
    return base + k_defl * math.sqrt(math.log(max(m, 1)) / max(n, 1))


def breadth(per_symbol_edge: dict[str, float], *, min_frac: float) -> tuple[float, list[str]]:
    """(frac_of_basket_with_positive_net_OOS_edge, passing symbols sorted)."""
    syms = sorted(per_symbol_edge)
    if not syms:
        return (0.0, [])
    passing = [s for s in syms if per_symbol_edge[s] > 0]
    return (len(passing) / len(syms), passing)


def param_neighbors(strategy: Strategy) -> list[Strategy]:
    """All +/-1 integer-param perturbations across every entry/exit/filter leaf. Invalid
    neighbors (e.g. fast >= slow) are dropped via the Strategy validators; duplicates removed."""
    base = strategy.model_dump()
    seen = {strategy.model_dump_json()}
    neighbors: list[Strategy] = []
    for slot in ("entry", "exit", "filter"):
        node = base.get(slot)
        if not node:
            continue
        is_comp = node.get("type") in ("all_of", "any_of")
        leaves = node["conditions"] if is_comp else [node]
        for li, leaf in enumerate(leaves):
            for field in _INT_FIELDS:
                if not isinstance(leaf.get(field), int):
                    continue
                for delta in (-1, 1):
                    cand = copy.deepcopy(base)
                    target = cand[slot]["conditions"][li] if is_comp else cand[slot]
                    target[field] = leaf[field] + delta
                    try:
                        s = Strategy.model_validate(cand)
                    except ValidationError:
                        continue
                    j = s.model_dump_json()
                    if j in seen:
                        continue
                    seen.add(j)
                    neighbors.append(s)
    return neighbors


def sum_curves(curves: list[list[EquityPoint]], base_equity: float) -> list[EquityPoint]:
    """THE shared portfolio-construction helper for BOTH Gate A and Gate B (trial.derive_portfolio
    imports this). Sum the per-symbol equity curves on the union time axis, carrying each curve's
    last-known equity forward across a gap (its pre-first value is `base_equity`). Using ONE
    construction keeps the two gates' DSR / drawdown thresholds directly comparable; the time-union
    alignment also fixes the index-alignment hazard of the old equal-weight average when symbols have
    different lengths/start dates. (DSR is computed on RETURNS and the drawdown metrics are
    percentage-based, so the absolute summed scale is irrelevant — for equal-start, fully-aligned
    per-symbol curves the resulting return series is identical to an equal-weight normalized average.)"""
    times = sorted({p.time for c in curves for p in c})
    maps = [{p.time: p.equity for p in c} for c in curves]
    last = [base_equity] * len(curves)
    out: list[EquityPoint] = []
    for t in times:
        for i, m in enumerate(maps):
            if t in m:
                last[i] = m[t]
        out.append(EquityPoint(time=t, equity=sum(last)))
    return out


def screen_one(strategy, bars_by_symbol: dict[str, list[dict]], *,
               cfg: GateAConfig = DEFAULT_GATE_A_CONFIG, cost: CostModel, m: int,
               require_cost_robust: bool = False,
               lockbox_bars_by_symbol: dict[str, list[dict]] | None = None) -> WalkForwardReport:
    """Full Gate-A campaign for ONE candidate over the basket: per-symbol folds -> equal-weight
    portfolio statistic -> CI-LB, DSR(m), regime coverage, breadth, DD/Ulcer/CDaR, optional
    param-neighbor + cost-robust + lockbox. Pure: m is supplied (the store owns the counter).

    Lockbox (§6.3): when the caller supplies no explicit `lockbox_bars_by_symbol`, the most recent
    `cfg.lockbox_frac` of each symbol's history is CARVED OFF as the confirmatory holdout — the
    screen (folds + full-window runs + param neighbors) sees only the remainder, and the reserved
    tail is scored once below, so the rail runs on the production path (which never passed a
    loader). Deterministic — a pure function of the input bars; pass the dict explicitly (or set
    lockbox_frac=0) to override."""
    num_leaves = leaf_count(strategy.entry) + leaf_count(strategy.exit) + leaf_count(strategy.filter)
    report = WalkForwardReport(symbol_scope="portfolio", m_at_eval=m, num_leaves=num_leaves)
    fails: list[str] = []

    if strategy.timeframe not in cfg.timeframe_allowed:
        fails.append("daily_only_v1")

    warmup = _warmup_bars(strategy, cfg)
    carve = lockbox_bars_by_symbol is None and cfg.lockbox_frac > 0
    lockbox_bars: dict[str, list[dict]] = dict(lockbox_bars_by_symbol or {})
    screen_bars_by_symbol: dict[str, list[dict]] = {}
    all_folds: list[WindowResult] = []
    all_returns: list[float] = []
    per_symbol_edge: dict[str, float] = {}   # DOLLAR edge per symbol (sign consumed by breadth)
    tradable_bars: list[int] = []            # per-symbol post-warmup screened span (Fix: verdict scaling)
    net_curves: list[list[EquityPoint]] = []
    gross_total = net_total = 0.0
    any_exit_seen = False

    for sym in sorted(bars_by_symbol):
        bars = bars_by_symbol[sym]
        if len(bars) < cfg.min_lookback_bars:      # judged on the FULL history, before any carve
            if "insufficient_history" not in fails:
                fails.append("insufficient_history")
            continue
        if carve:
            n_lb = int(len(bars) * cfg.lockbox_frac)
            if n_lb > 0:
                # holdout = the last n_lb bars; the eval slice keeps a warmup lead-in (indicators
                # only — holdout trades start after it, mirroring fold_metrics' embargo).
                lockbox_bars[sym] = bars[max(0, len(bars) - n_lb - warmup):]
                bars = bars[: len(bars) - n_lb]
        try:
            folds = make_folds(len(bars), k=cfg.k_folds, warmup=warmup,
                               min_fold_bars=cfg.min_fold_bars, embargo=warmup)
        except NotEnoughData:
            if "insufficient_history" not in fails:
                fails.append("insufficient_history")
            continue
        screen_bars_by_symbol[sym] = bars
        sym_folds = [fold_metrics(strategy, bars, f, warmup=warmup, cost=cost) for f in folds]
        all_folds += sym_folds

        net = replay.strict_run(strategy, bars, start_index=warmup, cost=cost)
        gross = replay.strict_run(strategy, bars, start_index=warmup, cost=NO_COST)
        net_attr = [t for t in net.trades if t.exit_reason != "end_of_data"]
        gross_attr = [t for t in gross.trades if t.exit_reason != "end_of_data"]
        any_exit_seen = any_exit_seen or any(t.exit_reason in ("signal", "stop", "target")
                                              for t in net.trades)
        all_returns += [t.return_pct for t in net_attr]
        per_symbol_edge[sym] = pooled_expectancy(sym_folds)
        tradable_bars.append(len(bars) - warmup)
        net_curves.append(net.equity_curve)
        net_total += sum(t.return_pct for t in net_attr)
        gross_total += sum(t.return_pct for t in gross_attr)

    portfolio = sum_curves(net_curves, base_equity=strategy.starting_equity)
    pooled = statistics.mean(all_returns) if all_returns else 0.0
    ci = expectancy_ci(all_returns, alpha=cfg.ci_alpha, seed=cfg.ci_seed)
    d = dsr(portfolio, m=m, ppy=cfg.ppy) if portfolio else 0.0
    breadth_frac, passing = breadth(per_symbol_edge, min_frac=cfg.breadth_min_frac)
    buckets = sorted({f.regime.bucket() for f in all_folds})
    closes = [p.equity for p in portfolio]
    # Complexity-adjusted OOS edge hurdle: a rule with more leaves must clear a proportionally HIGHER
    # net-edge floor (an Occam penalty against over-parameterized fits). Single-leaf -> factor 1.0
    # (unchanged). edge_floor is recorded as the adjusted hurdle so the report shows what was applied.
    base_floor = deflated_edge_floor(0.0, m, max(1, len(all_returns)), k_defl=cfg.k_defl)
    complexity_factor = 1.0 + cfg.complexity_hurdle_per_leaf * max(0, num_leaves - 1)
    edge_floor = base_floor * complexity_factor
    # pooled profit factor (return_pct-weighted gross wins / gross losses) for the pf floor gate.
    pos = sum(r for r in all_returns if r > 0)
    neg = sum(r for r in all_returns if r <= 0)
    pooled_pf = (pos / abs(neg)) if neg < 0 else (_PF_NO_LOSS if pos > 0 else 0.0)

    report.folds = all_folds
    report.pooled_trades = len(all_returns)
    report.pooled_expectancy = pooled            # PERCENT (mean trade return_pct)
    report.expectancy_ci = ci
    report.dsr = d
    report.edge_floor = edge_floor               # PERCENT (hurdle for net_edge)
    report.net_edge = pooled                     # PERCENT
    report.breadth_frac = breadth_frac
    report.breadth_pass_symbols = passing
    report.regime_buckets = buckets
    report.max_drawdown_pct = risk.max_drawdown(closes) or 0.0
    report.ulcer_index = risk.ulcer_index(closes) or 0.0
    report.conditional_drawdown_95 = risk.conditional_drawdown(closes) or 0.0
    # per-symbol trade count over the SCREENED span (verdict scales it to the forward window via
    # screened_bars — consuming it raw overestimated the forward expectation ~6x).
    report.expected_trades_over_window = (len(all_returns) / max(1, len(per_symbol_edge)))
    report.screened_bars = (sum(tradable_bars) / len(tradable_bars)) if tradable_bars else 0.0

    # ── gates -> fail_codes ──
    if report.pooled_trades < cfg.sanity_min_trades or report.pooled_trades < cfg.min_total_trades:
        fails.append("insufficient_sample")
    if strategy.exit is not None and not any_exit_seen and report.pooled_trades > 0:
        fails.append("degenerate_no_exit")
    if ci[0] <= 0:
        fails.append("ci_lb_negative")
    if report.net_edge <= report.edge_floor:
        fails.append("edge_below_floor")
    if report.pooled_trades > 0 and pooled_pf < cfg.pf_min:
        fails.append("pf_below_floor")
    if d < cfg.dsr_min:
        fails.append("dsr_below_floor")
    if not regime_coverage_ok(all_folds, min_buckets=cfg.regime_min_buckets,
                              require_high_vol=cfg.require_high_vol_regime):
        fails.append("insufficient_regime_coverage")
    if breadth_frac < cfg.breadth_min_frac:
        fails.append("breadth_fail")
    if report.max_drawdown_pct < cfg.graduate_max_dd_pct:
        fails.append("dd_breach")

    # cost robustness: zero-cost edge positive but stressed edge sign-flips
    report.cost_robust = not (gross_total > 0 and net_total <= 0)
    if require_cost_robust and not report.cost_robust:
        fails.append("cost_fragile")

    # param-neighbor robustness (only meaningful on a base passer; hard gate at graduation).
    # Neighbors probe the SCREENED bars (post-carve) so they never consume the lockbox either.
    if not fails and passing:
        first = passing[0]
        bars0 = screen_bars_by_symbol.get(first, bars_by_symbol[first])
        edges = []
        for nb in param_neighbors(strategy):
            nr = replay.strict_run(nb, bars0, start_index=_warmup_bars(nb, cfg), cost=cost)
            attr = [t for t in nr.trades if t.exit_reason != "end_of_data"]
            edges.append(statistics.mean([t.return_pct for t in attr]) if attr else 0.0)
        report.param_neighbor_median_edge = statistics.median(edges) if edges else None
        if (require_cost_robust and report.param_neighbor_median_edge is not None
                and report.param_neighbor_median_edge <= 0):
            fails.append("param_fragile")

    # lockbox: scored once (a confirmatory holdout; populated — carved by default — not a
    # screening gate here; verdict.lockbox_ok blocks graduation on a failed holdout)
    if lockbox_bars:
        lb_returns: list[float] = []
        for sym, lb in lockbox_bars.items():
            if len(lb) <= warmup:
                continue
            lr = replay.strict_run(strategy, lb, start_index=warmup, cost=cost)
            lb_returns += [t.return_pct for t in lr.trades if t.exit_reason != "end_of_data"]
        lb_exp = statistics.mean(lb_returns) if lb_returns else None
        report.lockbox = LockboxResult(evaluated=True, expectancy=lb_exp,
                                       num_trades=len(lb_returns),
                                       passed=(lb_exp is not None and lb_exp > 0))

    report.fail_codes = sorted(set(fails))
    report.passed = not report.fail_codes
    return report
