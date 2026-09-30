"""Pure analytics over loaded journal records. Deterministic; no I/O."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable

from webull_api.journal.pairing import _days_between, pair_fills
from webull_api.journal.schema import (
    Breakdown, ClosedTrade, DisciplineStats, Fill, JournalSummary, OptionTrade,
    PracticeDecision, StatBlock,
)


def option_trade_to_closed(t: OptionTrade) -> ClosedTrade:
    """Map a closed options round-trip into the unified ClosedTrade ledger (so options flow through
    the same win-rate / expectancy / breakdown analytics). Net prices are per-contract."""
    return ClosedTrade(
        symbol=t.underlying, source="paper_option", quantity=float(t.quantity),
        entry_price=t.open_net_price, exit_price=t.close_value,
        entry_at_iso=t.opened_at_iso, exit_at_iso=t.closed_at_iso,
        holding_days=_days_between(t.opened_at_iso, t.closed_at_iso),
        pnl=t.pnl, return_pct=t.return_pct, win=t.win,
        setup=f"option {t.strategy.lower()}", instrument="option",
        option_strategy=t.strategy, legs_desc=t.legs_desc, settle_basis=t.settle_basis,
        exit_fill_id=t.id)


def stat_block(trades: list[ClosedTrade]) -> StatBlock:
    n = len(trades)
    wins = [t for t in trades if t.win]
    losses = [t for t in trades if not t.win]
    win_rate = len(wins) / n if n else 0.0
    total_pnl = sum(t.pnl for t in trades)
    avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0.0
    expectancy = avg_win * win_rate + avg_loss * (1 - win_rate)
    expectancy_pct = sum(t.return_pct for t in trades) / n if n else 0.0
    return StatBlock(trades=n, wins=len(wins), win_rate=win_rate, total_pnl=total_pnl,
                     avg_win=avg_win, avg_loss=avg_loss, expectancy=expectancy,
                     expectancy_pct=expectancy_pct)


# A paper-book round trip held under an hour cannot be a decision: the paper engines only
# trade in the evening runner window, so entry and exit minutes apart means two runners
# traded against each other in one session (the 2026-07-21..23 coordination churn).
ARTIFACT_MAX_HOLD_MINUTES = 60.0


def _minutes_between(a_iso: str, b_iso: str) -> float | None:
    from datetime import datetime
    try:
        return (datetime.fromisoformat(b_iso) - datetime.fromisoformat(a_iso)).total_seconds() / 60.0
    except (ValueError, TypeError):
        return None


def is_coordination_artifact(t: ClosedTrade) -> bool:
    """True only for a positively-identified runner-coordination artifact: a PAPER trade whose
    hold was <= an hour. Real trades are never auto-excluded, and unparseable timestamps stay
    counted as decisions — bad data must not silently shrink a proof sample."""
    if t.source not in ("paper", "paper_option"):
        return False
    mins = _minutes_between(t.entry_at_iso, t.exit_at_iso)
    return mins is not None and 0 <= mins <= ARTIFACT_MAX_HOLD_MINUTES


def breakdown_by(trades: list[ClosedTrade], key_fn: Callable[[ClosedTrade], str]) -> list[Breakdown]:
    groups: dict[str, list[ClosedTrade]] = defaultdict(list)
    for t in trades:
        groups[key_fn(t)].append(t)
    out = [Breakdown(key=k, stats=stat_block(v)) for k, v in groups.items()]
    out.sort(key=lambda b: b.stats.total_pnl, reverse=True)
    return out


# Below this many completed practice signals, discipline numbers are noise, not a pattern: the
# manager's note records "Discipline: building (n/30 signals)" instead of a take-rate, and keeps
# discipline off the phone, until this many signals have accrued.
MIN_DISCIPLINE_SIGNALS = 30


def discipline_stats(decisions: list[PracticeDecision]) -> DisciplineStats:
    signals = len(decisions)
    taken = sum(1 for d in decisions if d.choice == "take")
    skipped = signals - taken
    take_rate = taken / signals if signals else 0.0
    pnl_captured = sum(d.signal_pnl or 0.0 for d in decisions if d.choice == "take")
    pnl_forgone = sum(d.signal_pnl or 0.0 for d in decisions if d.choice == "skip")
    skipped_winners = sum(1 for d in decisions if d.choice == "skip" and d.signal_win is True)
    taken_losers = sum(1 for d in decisions if d.choice == "take" and d.signal_win is False)
    return DisciplineStats(signals=signals, taken=taken, skipped=skipped, take_rate=take_rate,
                           pnl_captured=pnl_captured, pnl_forgone=pnl_forgone,
                           skipped_winners=skipped_winners, taken_losers=taken_losers)


def build_summary(fills: list[Fill], decisions: list[PracticeDecision], *,
                  option_trades: list[OptionTrade] | None = None,
                  generated_at_iso: str = "", top_symbols: int = 8, ledger_cap: int = 200,
                  last_sync: dict | None = None) -> JournalSummary:
    closed, opens = pair_fills(fills)
    closed = closed + [option_trade_to_closed(t) for t in (option_trades or [])]
    closed_sorted = sorted(closed, key=lambda t: t.exit_at_iso, reverse=True)
    # Best/worst over the FULL by-symbol breakdown: by_symbol is truncated to top_symbols for
    # display, so its last row is the Nth-best — the true losers can be truncated away.
    by_symbol_full = breakdown_by(closed, lambda t: t.symbol)
    return JournalSummary(
        generated_at_iso=generated_at_iso,
        overall=stat_block(closed),
        by_source=breakdown_by(closed, lambda t: t.source),
        by_symbol=by_symbol_full[:top_symbols],
        best_symbol=by_symbol_full[0] if by_symbol_full else None,
        worst_symbol=by_symbol_full[-1] if len(by_symbol_full) >= 2 else None,
        by_setup=breakdown_by(closed, lambda t: t.setup),
        closed_trades=closed_sorted[:ledger_cap],
        open_positions=opens,
        discipline=discipline_stats(decisions),
        practice_decisions=decisions,
        last_sync=last_sync or {})
