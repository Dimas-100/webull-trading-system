"""All lab config (frozen dataclasses + DEFAULT_* singletons + LAB_BASKET) and all lab pydantic
models, incl. the PaperProvenRecord handoff artifact. One source of truth for the §6.6 thresholds,
mirroring exits.ExitConfig / DEFAULT_EXIT_CONFIG."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from webull_api.strategy.schema import BacktestResult, EquityPoint, Strategy, Trade
from webull_api.journal.schema import Breakdown, StatBlock

# Integer leaf-param field names perturbed by gate_a.param_neighbors and bucketed by
# dedup.canon_bucket / propose._int_field_targets. Lives here (pure) rather than in gate_a so
# dedup/propose never have to import gate_a (which pulls in webull_api.risk and, from there,
# the market-data client) just for this constant.
_INT_FIELDS = ("fast", "slow", "period", "signal", "lookback")


# ──────────────────────────── config (frozen dataclasses) ────────────────────────────
@dataclass(frozen=True)
class CostConfig:
    slippage_pct: float = 0.05
    stop_slippage_pct: float = 0.10
    commission_flat: float = 0.0
    commission_per_share: float = 0.0
    min_commission: float = 0.0
    liquidity_adv_frac: float = 0.01


@dataclass(frozen=True)
class GateAConfig:
    timeframe_allowed: tuple[str, ...] = ("1D", "1W")
    min_lookback_bars: int = 750
    lockbox_frac: float = 0.15
    k_folds: int = 6
    min_fold_bars: int = 40
    warmup_buffer: int = 10
    min_total_trades: int = 100
    sanity_min_trades: int = 20
    dsr_min: float = 0.95
    regime_min_buckets: int = 2
    require_high_vol_regime: bool = True
    graduate_max_dd_pct: float = -12.0
    pf_min: float = 1.0
    breadth_min_frac: float = 0.5
    decay_mult: float = 1.5
    complexity_hurdle_per_leaf: float = 0.10
    require_cost_robust_screening: bool = False
    k_defl: float = 0.5
    ci_alpha: float = 0.05
    ci_seed: int = 0
    ppy: int = 252


@dataclass(frozen=True)
class GateBConfig:
    min_forward_bars: int = 126
    min_forward_trades: int = 12
    min_forward_trades_floor: int = 8
    min_profitable_window_frac: float = 0.6
    rolling_window_bars: int = 21
    graduate_max_dd_pct: float = -12.0
    kill_drawdown_pct: float = -20.0
    min_kill_sample: int = 20
    confirmed_regime_buckets: int = 2
    staleness_bars: int = 5
    fetch_floor: int = 400
    max_proving_bars: int = 1260
    dsr_min: float = 0.95
    # Multiple-testing count for the FORWARD-DSR gate. The forward paper window is a single
    # out-of-sample CONFIRMATION of a strategy Gate-A already selected — the search's multiple-
    # testing burden was paid at Gate-A, so the forward test is deflated by a small confirmatory
    # count (default 1), NOT the ever-growing Gate-A search count m_at_open (which would demand a
    # ~7 annualized Sharpe and make graduation a multi-year event, defeating the machine's purpose).
    # A constant also trivially preserves the anti-demotion property (a granted trial can't demote as
    # the global M grows). Set to None to restore the fully-coupled max-rigor behavior (deflate by
    # the trial's frozen m_at_open). NOTE: at the default the forward gate is P(Sharpe>0)>0.95 — an
    # implicit annualized-Sharpe floor ~26/sqrt(n-1) that eases as the forward window n grows (it is
    # one of 9 AND-conditions); raise this to 2-3 to keep the short-window bar higher.
    forward_dsr_m: int | None = 1
    ci_alpha: float = 0.05
    ppy: int = 252


@dataclass(frozen=True)
class LabConfig:
    starting_equity: float = 10000.0
    max_candidates_per_batch: int = 64
    max_concurrent_proving: int = 25
    explore_frac: float = 0.30
    explore_frac_floor: float = 0.10
    max_archetype_frac: float = 0.50
    max_behavioral_cohort_frac: float = 0.50
    seed_quota: int = 8
    kill_cooldown_days: tuple[int, ...] = (60, 180)
    retire_after_kills: int = 3
    behavioral_corr_threshold: float = 0.90
    canon_period_bucket: int = 5
    lineage_penalty: float = 0.10
    stagnation_patience: int = 3
    throttle_decay: float = 0.5
    throttle_floor: float = 0.0
    throttle_recover: float = 1.0
    pass_rate_default: float = 0.25
    pass_rate_alpha: float = 0.3
    pass_rate_floor: float = 0.05
    cohort_min_curve_points: int = 10
    # Min fraction of LAB_BASKET that must return bars for a cycle to proceed. A single flaky symbol
    # (common on a home connection) is tolerated and dropped; falling below this quorum is treated as
    # a real market-data outage and aborts the cycle (a hard, notifiable failure) rather than running
    # a breadth-starved screen.
    basket_min_ok_frac: float = 0.75


@dataclass(frozen=True)
class ScoreWeights:
    w1_profitable_window: float = 0.25
    w2_low_drawdown: float = 0.25
    w3_ci_lb_expectancy: float = 0.20
    w4_dsr: float = 0.20
    w5_regime_breadth: float = 0.10
    p1_turnover_drag: float = 0.10
    p2_generations_flag: float = 0.05
    proving_cap: float = 70.0


DEFAULT_COST_CONFIG = CostConfig()
DEFAULT_GATE_A_CONFIG = GateAConfig()
DEFAULT_GATE_B_CONFIG = GateBConfig()
DEFAULT_LAB_CONFIG = LabConfig()
DEFAULT_SCORE_WEIGHTS = ScoreWeights()

OBJECTIVE_TEXT = (
    "Optimize for a steady, low-drawdown, repeatable equity curve: positive expectancy that holds "
    "out-of-sample across diverse regimes, shallow drawdown, profitable across most independent windows "
    "— not raw return or one lucky streak."
)

# 2026-07-12 widening (lab-funnel-unblock spec): +LLY/COST/IWM (aligns with the paper runners'
# proven dip-buying basket in strategy/rsi2.py) and +TSLA/AMD (top-tier dollar liquidity, high
# beta — mean-reversion setups actually fire). GOOG deliberately absent (GOOGL covers the name);
# VOO skipped (≈SPY). Breadth still requires generalization across 50% of this (now wider) basket.
LAB_BASKET: tuple[str, ...] = (
    "SPY", "QQQ",
    "AAPL", "MSFT", "NVDA",
    "GOOGL",
    "AMZN", "HD",
    "PG", "KO",
    "JPM", "V",
    "JNJ", "UNH",
    "XOM",
    "CAT",
    "NEE",
    "LIN",
    "LLY", "COST", "IWM", "TSLA", "AMD",
)


# ──────────────────────────── verdict + core models ────────────────────────────
Verdict = Literal["proposed", "gate_a_failed", "proving", "provisional_proven",
                  "confirmed_proven", "rejected", "retired", "stale_data", "paused"]


class RegimeTag(BaseModel):
    trend: Literal["up", "down", "side"]
    vol: Literal["low", "high"]

    def bucket(self) -> str:
        return f"{self.trend}/{self.vol}"


@dataclass(frozen=True)
class Fold:
    index: int
    start: int
    end: int


class WindowResult(BaseModel):
    fold_index: int
    start_index: int
    end_index: int
    regime: RegimeTag
    num_trades: int
    expectancy: float                  # DOLLARS per trade (sum pnl / n); % lives in avg_trade_return_pct
    avg_trade_return_pct: float        # PERCENT per trade
    profit_factor: float
    win_rate: float
    return_pct: float
    max_drawdown_pct: float
    ulcer_index: float
    conditional_drawdown_95: float
    sharpe: float
    sortino: float


class LockboxResult(BaseModel):
    evaluated: bool = False
    expectancy: float | None = None    # PERCENT (mean holdout trade return_pct)
    num_trades: int = 0
    passed: bool | None = None


class WalkForwardReport(BaseModel):
    symbol_scope: Literal["portfolio", "single"] = "portfolio"
    folds: list[WindowResult] = Field(default_factory=list)
    pooled_trades: int = 0
    pooled_expectancy: float = 0.0     # PERCENT — mean trade return_pct pooled across the basket
    expectancy_ci: tuple[float, float] = (0.0, 0.0)   # PERCENT (CI over trade return_pct)
    dsr: float = 0.0
    m_at_eval: int = 0
    edge_floor: float = 0.0            # PERCENT — the deflated hurdle net_edge must clear
    net_edge: float = 0.0              # PERCENT (== pooled_expectancy)
    breadth_frac: float = 0.0
    breadth_pass_symbols: list[str] = Field(default_factory=list)
    regime_buckets: list[str] = Field(default_factory=list)
    num_leaves: int = 1
    max_drawdown_pct: float = 0.0
    ulcer_index: float = 0.0
    conditional_drawdown_95: float = 0.0
    # PER-SYMBOL trade count over the screened span (NOT a forward-window expectation — the
    # verdict scales it via screened_bars before comparing against forward trades).
    expected_trades_over_window: float = 0.0
    # mean per-symbol POST-WARMUP screened span in bars (0.0 on legacy reports -> verdict falls
    # back to the raw expected_trades_over_window).
    screened_bars: float = 0.0
    lockbox: LockboxResult | None = None
    param_neighbor_median_edge: float | None = None
    cost_robust: bool | None = None
    passed: bool = False
    fail_codes: list[str] = Field(default_factory=list)


class ForwardRun(BaseModel):
    result: BacktestResult
    forward_trades: int
    forward_bars: int
    gap_stop_slippage: float = 0.0
    illiquid_skips: int = 0


class TrialBook(BaseModel):
    scope: Literal["single", "portfolio"] = "portfolio"
    starting_equity: float
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    metrics: BacktestResult
    trades_by_symbol: dict[str, list[Trade]] = Field(default_factory=dict)
    per_symbol_metrics: dict[str, BacktestResult] = Field(default_factory=dict)
    forward_trades: int = 0
    forward_bars: int = 0
    gap_stop_slippage: float = 0.0
    illiquid_skips: int = 0


class ProvingState(BaseModel):
    trial_id: str
    strategy: Strategy
    basket: list[str] = Field(default_factory=list)
    bars_by_symbol: dict[str, list[dict]] = Field(default_factory=dict)
    forward_start_by_symbol: dict[str, int] = Field(default_factory=dict)
    inception_et_date: str
    started_at_iso: str
    last_advance_iso: str = ""
    bars_observed: int = 0
    coverage_gaps: list[dict] = Field(default_factory=list)
    status: Verdict = "proving"
    gate_a: WalkForwardReport | None = None
    book: TrialBook | None = None
    regime_buckets_seen: list[str] = Field(default_factory=list)
    m_at_open: int = 0   # multiple-testing count frozen at trial open (forward-DSR penalty; RT-1)


class ConsistencyScore(BaseModel):
    score: float
    profitable_window_frac: float
    normalized_drawdown: float
    ci_lb_expectancy: float
    dsr: float
    regime_breadth_frac: float
    turnover_drag: float
    generations_survived: int
    capped: bool


class ProposalRecord(BaseModel):
    parent_id: str | None = None
    parent_fingerprint: str | None = None
    why: str = ""
    mutation: str | None = None
    generation: int = 0
    seeded_from: Literal["fresh", "provisional_proven", "confirmed_proven"] = "fresh"


class TrialPerf(BaseModel):
    id: str
    name: str
    status: Verdict
    stats: StatBlock
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    score: float | None = None
    forward_trades: int = 0
    forward_bars: int = 0


class StrategyRecord(BaseModel):
    id: str
    strategy: Strategy
    fingerprint: str
    canon_bucket: str
    behavioral_cohort: str | None = None
    archetype: str
    generation: int = 0
    cohort: str
    lineage: ProposalRecord | None = None
    status: Verdict = "proposed"
    created_at_iso: str
    as_of: str
    gate_a: WalkForwardReport | None = None
    trial_id: str | None = None
    trial_summary: TrialPerf | None = None
    score: ConsistencyScore | None = None
    kills: int = 0
    cooldown_until_iso: str | None = None
    fail_codes: list[str] = Field(default_factory=list)
    origin: Literal["lab_generated"] = "lab_generated"


class LabSummary(BaseModel):
    generated_at_iso: str
    overall: StatBlock
    by_strategy: list[Breakdown] = Field(default_factory=list)
    by_cohort: list[Breakdown] = Field(default_factory=list)
    by_generation: list[Breakdown] = Field(default_factory=list)
    by_behavioral_cohort: list[Breakdown] = Field(default_factory=list)
    trials: list[TrialPerf] = Field(default_factory=list)


# ──────────────────────────── proposal brief (leak-safe) ────────────────────────────
class SeedEntry(BaseModel):
    fingerprint: str
    strategy: Strategy
    archetype: str
    why: str
    status: Literal["provisional_proven", "confirmed_proven"]


class ArchetypeAggregate(BaseModel):
    archetype: str
    lifetime_proposed: int
    lifetime_proving: int
    lifetime_proven: int
    lifetime_killed: int


class ShapedFeedback(BaseModel):
    fold_pass_count: int = 0
    fold_fail_count: int = 0
    most_failing_condition: str | None = None
    common_fail_codes: list[str] = Field(default_factory=list)


class ProposalBrief(BaseModel):
    schema_version: int = 1
    as_of: str
    regime_now: RegimeTag
    universe: list[str]
    objective: str
    archetype_stats: list[ArchetypeAggregate] = Field(default_factory=list)
    seeds: list[SeedEntry] = Field(default_factory=list)
    explore_quota: int = 0
    avoid_fingerprints: list[str] = Field(default_factory=list)
    tombstone_patterns: list[str] = Field(default_factory=list)
    feedback: ShapedFeedback = Field(default_factory=ShapedFeedback)
    avoid_canon_buckets: list[str] = Field(default_factory=list)


# ──────────────────────────── orchestrator results ────────────────────────────
class IngestResult(BaseModel):
    accepted: list[str] = Field(default_factory=list)
    gate_a_failed: list[dict] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)
    m_after: int = 0


class AdvanceResult(BaseModel):
    advanced: list[str] = Field(default_factory=list)
    promoted: list[str] = Field(default_factory=list)
    killed: list[str] = Field(default_factory=list)
    no_op: bool = False


class ReseedResult(BaseModel):
    seeds: list[SeedEntry] = Field(default_factory=list)
    explore_quota: int = 0
    archetype_quota: dict[str, int] = Field(default_factory=dict)


# ──────────────────────────── autopilot cycle (additive; pure data) ────────────────────────────
class StagnationState(BaseModel):
    schema_version: int = 1
    overall_dry_streak: int = 0
    per_archetype_dry: dict[str, int] = Field(default_factory=dict)
    throttle: dict[str, float] = Field(default_factory=dict)
    pass_rate_ewma: float = 0.0
    cycles_run: int = 0
    last_cycle_date: str = ""        # persisted under meta['stagnation']; {} validates a fresh state


class BatchDecision(BaseModel):
    active_trials: int
    admitted_waiting: int
    open_slots: int
    pass_rate_est: float
    mutation_n: int
    explore_n: int
    max_admit: int
    cohort_caps: dict[str, int] = Field(default_factory=dict)
    cold_start: bool = False


class CycleReport(BaseModel):
    schema_version: int = 1
    cycle_seq: int                   # monotone run counter (meta['cycle_seq'] + 1)
    cycle_date: str                  # today_et "YYYY-MM-DD"
    ran_at_iso: str                  # now_iso
    regime: RegimeTag
    no_op: bool = False              # advance produced no advance (mirrors AdvanceResult.no_op)
    no_bar: bool = False             # no new confirmed bar closed (== adv.no_op)
    advanced: list[str] = Field(default_factory=list)
    promoted: list[str] = Field(default_factory=list)
    killed: list[str] = Field(default_factory=list)
    generated: int = 0
    explore_generated: int = 0
    screened: int = 0                # == m_after - m_before (non-dup Gate-A campaigns)
    duplicates: int = 0
    gate_a_failed: list[dict] = Field(default_factory=list)   # [{"fingerprint","fail_codes"}]
    accepted: list[str] = Field(default_factory=list)         # fingerprints (== record ids)
    new_proving: list[str] = Field(default_factory=list)      # ids that opened a trial this cycle
    proving_active: int = 0
    slots_cap: int = 25
    free_slots: int = 0
    backlog_submitted: int = 0       # == len(new_proving)
    decision: BatchDecision
    m_before: int
    m_after: int
    stagnation: StagnationState
    throttled_archetypes: list[str] = Field(default_factory=list)  # archetypes with throttle < 1.0
    events: list[dict] = Field(default_factory=list)          # append-only events the shell flushes
    errors: list[str] = Field(default_factory=list)
    elapsed_ms: int = 0

    def one_line(self) -> str:
        """Single stdout line for the CLI."""
        line = (
            f"cycle #{self.cycle_seq} {self.cycle_date}: adv {len(self.advanced)}/prom "
            f"{len(self.promoted)}/kill {len(self.killed)} · gen {self.generated}→acc "
            f"{len(self.accepted)}→proving {len(self.new_proving)} · slots "
            f"{self.proving_active}/{self.slots_cap} · M {self.m_before}→{self.m_after} · "
            f"{self.regime.bucket()}"
        )
        if self.throttled_archetypes:
            line += f" · throttled {','.join(self.throttled_archetypes)}"
        if self.errors:
            line += f" · ERRORS {len(self.errors)}"
        return line


# ──────────────────────────── handoff artifact (data only; pre-installs the human gate) ──────────────────
class PaperProvenRecord(BaseModel):
    schema_version: int = 1
    verdict: Literal["confirmed_proven"] = "confirmed_proven"
    graduated_at: str
    strategy: Strategy
    lineage: ProposalRecord | None = None
    gate_a: WalkForwardReport
    gate_b: dict
    assumptions: dict
    objective_met: dict
    m_at_graduation: int = 0
    promotion: None = None
    capital_allocation: None = None
    live_authorization: None = None
    human_promotion_authorized: bool = False
    promoted_by: str = "human"
