"""Gate-B verdict state machine — PURE function of a trial's reports (gate_a report + derived book +
observed forward regime buckets). The harness, never the LLM, decides 'proven'. No I/O, no order path.
Staleness (a calendar judgement) is set upstream by the orchestrator; evaluate_verdict respects an
already-set stale_data status but never re-derives it (it has no calendar access)."""
from __future__ import annotations

from webull_api.lab.gate_a import dsr, expectancy_ci
from webull_api.lab.schema import (DEFAULT_GATE_A_CONFIG, DEFAULT_GATE_B_CONFIG, PaperProvenRecord,
                                   ProvingState, StrategyRecord, TrialBook, Verdict)
from webull_api.risk import conditional_drawdown, ulcer_index


def rolling_window_profit_frac(equity_curve, *, window: int) -> float:
    """Fraction of contiguous non-overlapping bar-index sub-windows whose end equity > start equity."""
    n = len(equity_curve)
    if n < 2 or window < 1:
        return 0.0
    wins = total = 0
    for i in range(0, n - 1, window):
        seg = equity_curve[i:i + window + 1]
        if len(seg) < 2:
            continue
        total += 1
        if seg[-1].equity > seg[0].equity:
            wins += 1
    return wins / total if total else 0.0


def decay_ok(forward_expectancy: float, backtest_expectancy: float) -> bool:
    """Forward expectancy keeps the Gate-A sign and is not catastrophically below it (>=50% envelope).
    BOTH arguments are in PERCENT (mean trade return_pct) — comparing the book's dollar expectancy
    against Gate-A's percent figure degenerated the envelope into a sign check."""
    if backtest_expectancy <= 0:
        return forward_expectancy > 0
    if forward_expectancy <= 0:
        return False
    return forward_expectancy >= 0.5 * backtest_expectancy


def regime_buckets_in_forward(state: ProvingState) -> list[str]:
    """Distinct regime buckets observed across the forward window (sorted, deduped)."""
    return sorted(set(state.regime_buckets_seen))


def _forward_ci(book: TrialBook, cfg_b) -> tuple[float, float]:
    # DOLLAR CI (over trade pnl) — consumed as sign-only checks (ci[0] > 0 / ci[1] < 0), so the
    # unit is safe; the decay gate below uses the PERCENT expectancy instead.
    pnls = [t.pnl for trades in book.trades_by_symbol.values() for t in trades]
    if not pnls:
        return (0.0, 0.0)
    return expectancy_ci(pnls, alpha=cfg_b.ci_alpha, seed=0)


def _forward_expectancy_pct(book: TrialBook) -> float:
    """Mean forward trade return in PERCENT — like units with WalkForwardReport.pooled_expectancy
    (the decay gate must never compare $ to %; book.metrics.expectancy is DOLLARS)."""
    rets = [t.return_pct for trades in book.trades_by_symbol.values() for t in trades]
    return sum(rets) / len(rets) if rets else 0.0


def evaluate_verdict(state: ProvingState, *, cfg_a=DEFAULT_GATE_A_CONFIG,
                     cfg_b=DEFAULT_GATE_B_CONFIG, m: int) -> Verdict:
    if state.status == "stale_data":
        return "stale_data"
    book = state.book
    if book is None:
        return "proving"

    metrics = book.metrics
    fwd_trades = book.forward_trades
    fwd_bars = book.forward_bars
    max_dd = metrics.max_drawdown_pct
    ci = _forward_ci(book, cfg_b)

    # ── KILL rules (any sample size) ──
    if max_dd <= cfg_b.kill_drawdown_pct:
        return "rejected"
    if fwd_trades >= cfg_b.min_kill_sample and ci[1] < 0:
        return "rejected"

    # ── adaptive trade-count floor (infrequent rule not penalized) ──
    # Gate-A's expected_trades_over_window is a PER-SYMBOL count over the ~750-bar screened span;
    # the forward book pools the SAME basket over fwd_bars. Scale the POOLED Gate-A trade count to
    # the observed forward window (like units) — consuming the raw figure overestimated ~6x, so the
    # floor never adapted for infrequent rules. Legacy reports without screened_bars fall back to
    # the old raw consumption (backward compatible).
    ga = state.gate_a
    if ga is not None and ga.screened_bars > 0:
        expected = int(ga.pooled_trades * fwd_bars / ga.screened_bars)
    else:
        expected = int(ga.expected_trades_over_window) if ga else cfg_b.min_forward_trades
    adaptive_floor = max(min(cfg_b.min_forward_trades, expected), cfg_b.min_forward_trades_floor)

    # ── Gate-B PASS predicate ──
    bt_expectancy = state.gate_a.pooled_expectancy if state.gate_a else 0.0   # PERCENT
    dd_ref = state.gate_a.max_drawdown_pct if state.gate_a else 0.0
    dd_envelope_ok = dd_ref == 0.0 or max_dd >= dd_ref * cfg_a.decay_mult
    profit_frac = rolling_window_profit_frac(book.equity_curve, window=cfg_b.rolling_window_bars)
    lockbox_ok = (state.gate_a is None or state.gate_a.lockbox is None
                  or state.gate_a.lockbox.passed is not False)
    # Forward-DSR multiple-testing count. The forward paper window is a single out-of-sample
    # CONFIRMATION of a strategy Gate-A already selected, so the search's multiple-testing burden was
    # already paid at Gate-A — the forward test is deflated by a small confirmatory count
    # (cfg_b.forward_dsr_m, default 1), NOT the ever-growing Gate-A search count. A constant also
    # trivially preserves the anti-demotion property (a granted trial cannot demote as the global M
    # grows). cfg_b.forward_dsr_m=None restores the fully-coupled max-rigor behavior: deflate by the
    # count FROZEN at trial open (m_at_open), falling back to the passed `m` only when it is unset.
    m_fwd = cfg_b.forward_dsr_m if cfg_b.forward_dsr_m is not None else (state.m_at_open or m)
    forward_dsr = dsr(book.equity_curve, m=m_fwd, ppy=cfg_b.ppy) if book.equity_curve else 0.0

    passed = (
        fwd_trades >= adaptive_floor
        and fwd_bars >= cfg_b.min_forward_bars
        and ci[0] > 0
        and max_dd > cfg_b.graduate_max_dd_pct
        and dd_envelope_ok
        and profit_frac >= cfg_b.min_profitable_window_frac
        and decay_ok(_forward_expectancy_pct(book), bt_expectancy)   # LIKE units: % vs %
        and lockbox_ok
        and forward_dsr > cfg_b.dsr_min
    )
    if passed:
        if len(regime_buckets_in_forward(state)) >= cfg_b.confirmed_regime_buckets:
            return "confirmed_proven"
        return "provisional_proven"

    # ── time-stop ONLY for a no-edge trial (never on a positive-but-slow one) ──
    if fwd_bars >= cfg_b.max_proving_bars and ci[0] <= 0:
        return "rejected"
    return "proving"


def build_proven_record(rec: StrategyRecord, state: ProvingState, *, graduated_at_iso: str,
                        m_at_graduation: int, promoted_by: str = "human") -> PaperProvenRecord:
    """ONLY callable for a 'confirmed_proven' state (raises otherwise). The clean data-only handoff:
    a frozen rule snapshot + Gate-A/Gate-B evidence, with the human-promotion gate PRE-INSTALLED off
    (promotion/capital_allocation/live_authorization=None, human_promotion_authorized=False) — the
    later real-money spec is the sole writer of those slots. The forward edge is carried as an
    UPPER BOUND (simulated fills, single STRESS_COST)."""
    if state.status != "confirmed_proven":
        raise ValueError(f"build_proven_record requires a confirmed_proven state, got {state.status!r}")
    book = state.book
    closes = [p.equity for p in book.equity_curve]
    ci = _forward_ci(book, DEFAULT_GATE_B_CONFIG)
    buckets = regime_buckets_in_forward(state)
    pooled = [t for trades in book.trades_by_symbol.values() for t in trades]
    gate_b = {
        "trial_id": state.trial_id,
        "started_at": state.started_at_iso,
        "ended_at": graduated_at_iso,
        "bars_observed": state.bars_observed,
        "coverage_gaps": list(state.coverage_gaps),
        "live_metrics": book.metrics.model_dump(),
        "forward_expectancy_ci": [ci[0], ci[1]],
        "live_equity_curve": [p.model_dump() for p in book.equity_curve],
        "live_trades": [t.model_dump() for t in pooled],
        "regime_flips_survived": max(len(buckets) - 1, 0),
    }
    assumptions = {
        "fills": "simulated next-open",
        "cost": "STRESS_COST (slippage + commission + gap-stop + liquidity cap)",
        "breadth": "equal-weight LAB_BASKET portfolio",
        "single_symbol": False,
        "note": ("Forward expectancy is an UPPER BOUND: simulated fills on a single stress-cost model, "
                 "not an execution-realism proof. Real placement is a separate, human-authorized step."),
    }
    objective_met = {
        "profitable_window_frac": rolling_window_profit_frac(
            book.equity_curve, window=DEFAULT_GATE_B_CONFIG.rolling_window_bars),
        "ulcer_index": ulcer_index(closes),
        "conditional_drawdown_95": conditional_drawdown(closes, 0.95),
        "live_expectancy_ci_lb": ci[0],
    }
    return PaperProvenRecord(
        graduated_at=graduated_at_iso, strategy=rec.strategy, lineage=rec.lineage,
        gate_a=state.gate_a, gate_b=gate_b, assumptions=assumptions, objective_met=objective_met,
        m_at_graduation=m_at_graduation, promoted_by=promoted_by)
