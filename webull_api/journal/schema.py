"""Pydantic v2 models for the learning-layer journal + the pure setup_label helper.

Two layers: STORED records (Fill, PracticeDecision — enriched once at sync) and DERIVED
views (ClosedTrade, OpenPosition, StatBlock, Breakdown, DisciplineStats, JournalSummary —
computed fresh by analytics, never persisted)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MarketContext(BaseModel):
    """Best-effort technicals at action time; all optional (null if bars unavailable)."""
    as_of_iso: str | None = None
    trend: str | None = None          # "uptrend" | "downtrend" | "sideways"
    rsi14: float | None = None
    sma20: float | None = None
    sma50: float | None = None
    pct_change_5: float | None = None
    support: list[float] | None = None
    resistance: list[float] | None = None


class ThesisRecord(BaseModel):
    """Optional trader thesis captured at decision time (entry). Confidence is REQUIRED
    when a thesis is provided; all other fields are optional free-form annotations."""
    confidence: int                          # 1..5, REQUIRED if thesis is given
    direction: Literal["LONG"] = "LONG"     # long-only for now
    expected_move_pct: float | None = None
    horizon: str | None = None              # free text: "intraday" | "days" | "weeks"
    invalidation: str | None = None         # thesis-break / where you're wrong
    setup: str | None = None                # e.g. "breakout", "pullback"
    note: str | None = None


class Fill(BaseModel):
    """One executed BUY/SELL — the atom that feeds FIFO pairing. Real + paper share this."""
    id: str
    source: Literal["real", "paper"]
    account_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    quantity: float
    price: float
    filled_at_iso: str
    order_type: str
    context: MarketContext | None = None
    thesis: ThesisRecord | None = None
    # Provenance for a mechanically-placed fill (the proven-strategy paper runner): which Lab-proven
    # strategy / trial made it. None for discretionary/RSI2/real fills. Optional so old JSONL parses.
    strategy_id: str | None = None
    trial_id: str | None = None


class PracticeDecision(BaseModel):
    """A Take/Skip decision WITH the forward outcome from the rules benchmark.
    Ingested only from COMPLETED practice sessions."""
    id: str
    session_id: str
    strategy_name: str | None = None
    symbol: str
    decided_at_iso: str
    choice: Literal["take", "skip"]
    signal_why: str
    entry_price: float | None = None
    signal_pnl: float | None = None
    signal_win: bool | None = None
    context: MarketContext | None = None
    thesis: ThesisRecord | None = None
    mfe: float | None = None
    mae: float | None = None


class OptionTrade(BaseModel):
    """A closed options round-trip (open->close or open->expiration) with realized P&L. STORED (like
    Fill), deduped by the close event's id. The options-paper account history is capped, so these are
    persisted permanently in their own log. net prices are per-contract (+debit / −credit)."""
    id: str                                   # the close/expiration order's paper_order_id
    source: Literal["paper_option"] = "paper_option"
    underlying: str
    strategy: Literal["SINGLE", "VERTICAL"]
    legs_desc: str                            # compact label, e.g. "AAPL +300C / -310C 2026-07-17"
    quantity: int                             # contracts closed
    open_net_price: float
    close_value: float
    opened_at_iso: str
    closed_at_iso: str
    pnl: float
    win: bool
    return_pct: float                         # percent (×100), = pnl / risk_basis · 100
    reason: Literal["closed", "expired"]
    # Which price the expiration settlement used (reason="expired" only; None on "closed" and
    # pre-feature records): the expiration-day close vs the spot at whatever refresh settled it.
    settle_basis: Literal["exp_close", "current_spot"] | None = None
    # Set only on a manually-reconstructed historical row (a close that predates the realized-P&L
    # stamp fields, so the engine/normalizer could never have ingested it automatically). None on
    # every engine-ingested row. Truthy `note` is also the signal north_star._paper_inputs() uses
    # to keep a backfilled row out of the live proof-bar/expectancy sample (see that module) —
    # it's a corrected historical record, not a decision the current strategy produced.
    note: str | None = None


class ClosedTrade(BaseModel):
    """A round-trip: equity FIFO (BUY opens, SELL closes) OR a mapped options round-trip."""
    symbol: str
    source: Literal["real", "paper", "paper_option", "paper_trial"]
    quantity: float
    entry_price: float
    exit_price: float
    entry_at_iso: str
    exit_at_iso: str
    holding_days: float
    pnl: float
    return_pct: float
    win: bool
    entry_context: MarketContext | None = None
    setup: str = "unknown"
    thesis: ThesisRecord | None = None
    mfe: float | None = None
    mae: float | None = None
    instrument: Literal["equity", "option"] = "equity"
    option_strategy: str | None = None        # "SINGLE" | "VERTICAL" (options only)
    legs_desc: str | None = None              # compact legs label (options only)
    settle_basis: Literal["exp_close", "current_spot"] | None = None  # expired-options price basis
    # NEW — lab attribution (all optional/defaulted so existing JSONL still validates):
    strategy_id: str | None = None
    generation: int | None = None
    cohort: str | None = None
    behavioral_cohort: str | None = None
    trial_id: str | None = None
    # Fill-id provenance (additive; None on legacy rows): the exact join key back to the
    # action log's `ref` — lets a consumer attribute each side of a round-trip to the
    # runner that placed it (webull_api/scorecard.py). Options carry exit only (the
    # OptionTrade record does not retain the open order id).
    entry_fill_id: str | None = None
    exit_fill_id: str | None = None


class OpenPosition(BaseModel):
    """Un-exited BUY remainder; excluded from win-rate."""
    symbol: str
    source: Literal["real", "paper"]
    quantity: float
    avg_entry_price: float
    opened_at_iso: str


class StatBlock(BaseModel):
    trades: int
    wins: int
    win_rate: float
    total_pnl: float
    avg_win: float
    avg_loss: float
    expectancy: float
    # Size-independent expectancy: mean per-trade return_pct. Dollar expectancy over mixed
    # lot sizes ($5k paper lots vs $40 real orders) says nothing about the edge; this does.
    expectancy_pct: float = 0.0


class Breakdown(BaseModel):
    key: str
    stats: StatBlock


class DisciplineStats(BaseModel):
    signals: int
    taken: int
    skipped: int
    take_rate: float
    pnl_captured: float
    pnl_forgone: float
    skipped_winners: int
    taken_losers: int


class JournalSummary(BaseModel):
    generated_at_iso: str
    overall: StatBlock
    by_source: list[Breakdown]
    by_symbol: list[Breakdown]
    by_setup: list[Breakdown]
    closed_trades: list[ClosedTrade]
    open_positions: list[OpenPosition]
    discipline: DisciplineStats
    practice_decisions: list[PracticeDecision]
    last_sync: dict
    # Best/worst symbol over the FULL by-symbol breakdown — by_symbol is truncated for display
    # (top_symbols), so its last row is the Nth-best, not the true weakest. Optional/defaulted
    # so old serialized summaries still parse.
    best_symbol: Breakdown | None = None
    worst_symbol: Breakdown | None = None


def setup_label(ctx: MarketContext | None) -> str:
    """Derive a 'setup' label from entry context: trend × RSI-zone. 'unknown' if no context."""
    if ctx is None or ctx.trend is None:
        return "unknown"
    rsi = ctx.rsi14
    if rsi is None:
        zone = "RSI?"
    elif rsi < 30:
        zone = "RSI<30"
    elif rsi > 70:
        zone = "RSI>70"
    else:
        zone = "RSI mid"
    return f"{ctx.trend} · {zone}"
