"""Pure weekly strategy-scorecard engine: attribute closed paper/options trades to the
strategy that produced them, compute sample-gated per-bucket stats, pair the RSI2
equity-vs-options expressions, and evaluate deterministic suggestion rules. No I/O, no
network — mirrors lab/lab_analytics.py. Consumed by webull_web/scorecard_service.py.
Spec: docs/superpowers/specs/2026-07-25-strategy-scorecard-design.md"""
from __future__ import annotations

from dataclasses import dataclass

from webull_api.journal import analytics
from webull_api.journal.schema import ClosedTrade


@dataclass(frozen=True)
class ScorecardConfig:
    eval_weekday: int = 4          # Friday ET — the suite's weekly full-eval day
    min_bucket_n: int = 10         # below this a bucket is "building": stats shown, rules skipped
    min_pairs: int = 8             # expression A/B needs this many (symbol, entry-date) pairs
    expression_gap_pp: float = 15.0  # mean-return gap (pp) that flags the lagging expression
    min_stop_n: int = 5            # equity stop-slippage rule needs this many stop exits
    stop_slippage_pp: float = 2.0  # mean stop exit worse than plan by more than this flags
    plan_stop_pct: float = -8.0    # the equity plan stop the slippage rule compares against
    option_stop_breach_pct: float = -60.0  # option close at/below this gapped through the -50% stop
    unchecked_window: int = 10     # last-N rsi2-options entries for the vol-unchecked share
    unchecked_share: float = 0.5   # flag when more than this fraction were IV-unchecked
    artifact_window_days: int = 7  # fresh-artifact rule looks back this many calendar days


def index_actions(actions: list[dict]) -> tuple[dict, set]:
    """(actions_by_ref, options_entry_days). by_ref: first row per ref wins (mirrors
    activity.join_why). options_entry_days: {(symbol, ts-date)} of runner:options_entry
    trade rows — the options entry join is by day because OptionTrade does not retain
    the open order id (spec §1)."""
    by_ref: dict = {}
    entry_days: set = set()
    for a in actions:
        ref = a.get("ref")
        if ref and ref not in by_ref:
            by_ref[ref] = a
        if a.get("source") == "runner:options_entry" and a.get("kind") == "trade":
            entry_days.add((a.get("symbol"), str(a.get("ts", ""))[:10]))
    return by_ref, entry_days


def bucket_of(t: ClosedTrade, actions_by_ref: dict, options_entry_days: set) -> str:
    """The strategy bucket a closed trade belongs to. Precedence: explicit strategy_id
    (proven shelf) > instrument-specific entry attribution > discretionary."""
    if t.strategy_id:
        return f"proven:{t.strategy_id}"
    if t.instrument == "option":
        if (t.symbol, (t.entry_at_iso or "")[:10]) in options_entry_days:
            return "rsi2-options"
        return "options-discretionary"
    entry = actions_by_ref.get(t.entry_fill_id) if t.entry_fill_id else None
    if entry is None:
        return "unattributed-equity"
    if entry.get("source") == "runner:rsi2":
        return "rsi2-equity"
    if entry.get("source") == "runner:rsi2_real":
        # The REAL sleeve's own lots (attribution rows written at ledger adoption,
        # rsi2_real_attribution). Kept apart from rsi2-equity: the proof bar reads paper only.
        return "rsi2-real"
    return "discretionary-equity"


def expression_pairs(equity: list[ClosedTrade], options: list[ClosedTrade]) -> dict:
    """RSI2 expression A/B: rsi2-equity vs rsi2-options closed trades matched on
    (symbol, entry date). return_pct is return-on-capital-committed on both sides
    (equity pnl/cost; options pnl/debit) — comparable when labeled as such. When partial
    exits produce several equity round-trips on one (symbol, day), the FIRST represents
    the equity side; the extras count as unpaired."""
    eq_by: dict = {}
    for t in equity:
        eq_by.setdefault((t.symbol, (t.entry_at_iso or "")[:10]), t)
    pairs = []
    for o in options:
        k = (o.symbol, (o.entry_at_iso or "")[:10])
        if k in eq_by:
            pairs.append((eq_by[k], o))
    n = len(pairs)
    out = {"pairs_n": n, "equity_unpaired": len(equity) - n,
           "options_unpaired": len(options) - n,
           "equity_mean_return_pct": None, "options_mean_return_pct": None,
           "equity_win_rate": None, "options_win_rate": None}
    if n:
        out["equity_mean_return_pct"] = round(sum(e.return_pct for e, _ in pairs) / n, 2)
        out["options_mean_return_pct"] = round(sum(o.return_pct for _, o in pairs) / n, 2)
        out["equity_win_rate"] = round(sum(1 for e, _ in pairs if e.win) / n, 4)
        out["options_win_rate"] = round(sum(1 for _, o in pairs if o.win) / n, 4)
    return out


def _flag(rule: str, severity: str, evidence: str, suggestion: str) -> dict:
    return {"rule": rule, "severity": severity, "evidence": evidence,
            "suggestion": suggestion}


def _days_ago(day: str, today: str) -> float:
    from datetime import date
    try:
        return (date.fromisoformat(today) - date.fromisoformat(day)).days
    except ValueError:
        return float("inf")


def evaluate_rules(buckets: dict, pairs: dict, trades_by_bucket: dict,
                   actions_by_ref: dict, options_entry_actions: list[dict],
                   excluded: list, today: str, cfg: ScorecardConfig) -> list[dict]:
    """Deterministic, sample-gated suggestion flags. Flags INFORM — nothing auto-acts."""
    flags: list[dict] = []

    # 1) negative expectancy on a mature bucket -> review/pause candidate
    for name, b in sorted(buckets.items()):
        if not b["building"] and b["expectancy_pct"] < 0:
            flags.append(_flag(
                "negative_expectancy", "warn",
                f"{name}: n={b['trades']}, expectancy {b['expectancy_pct']:+.1f}%/trade "
                f"(${b['expectancy']:+.0f})",
                f"{name} has negative expectancy at a mature sample — review/pause candidate"))

    # 2) expression A/B gap (rsi2-equity vs rsi2-options on the same signals)
    if pairs["pairs_n"] >= cfg.min_pairs:
        gap = pairs["options_mean_return_pct"] - pairs["equity_mean_return_pct"]
        if abs(gap) > cfg.expression_gap_pp:
            lagging = "options" if gap < 0 else "equity"
            flags.append(_flag(
                "expression_gap", "warn",
                f"{pairs['pairs_n']} paired signals: equity "
                f"{pairs['equity_mean_return_pct']:+.1f}% vs options "
                f"{pairs['options_mean_return_pct']:+.1f}% per trade",
                f"the {lagging} expression of the RSI2 signal is lagging by "
                f"{abs(gap):.1f}pp — review whether it earns its slot"))

    # 3a) equity stop slippage: realized stops materially worse than the plan stop
    # population = the paper_eod exit discipline (its -8% plan is what plan_stop_pct measures), across ALL buckets — a proven:* bucket's paper_eod-stopped trades count too; exits via other runners have their own stops and are out of scope.
    stop_exits = []
    for ts_ in trades_by_bucket.values():
        for t in ts_:
            if t.instrument != "equity":
                continue
            ex = actions_by_ref.get(t.exit_fill_id) if t.exit_fill_id else None
            if ex and ex.get("source") == "runner:paper_eod" and "stop" in str(ex.get("why", "")).lower():
                stop_exits.append(t)
    if len(stop_exits) >= cfg.min_stop_n:
        mean_ret = sum(t.return_pct for t in stop_exits) / len(stop_exits)
        if mean_ret < cfg.plan_stop_pct - cfg.stop_slippage_pp:
            flags.append(_flag(
                "stop_slippage", "warn",
                f"{len(stop_exits)} stop exits realized {mean_ret:+.1f}% avg vs the "
                f"{cfg.plan_stop_pct:g}% plan",
                "EOD stops are realizing materially past the plan — size to the realized "
                "risk, or tighten the evaluation cadence assumption"))

    # 3b) options gapped through the -50% stop (raw fact — reports from the first one)
    breaches = [t for ts_ in trades_by_bucket.values() for t in ts_
                if t.instrument == "option" and t.return_pct <= cfg.option_stop_breach_pct]
    if breaches:
        flags.append(_flag(
            "option_stop_breach", "info",
            f"{len(breaches)} option close(s) at or below "
            f"{cfg.option_stop_breach_pct:g}% (worst {min(t.return_pct for t in breaches):+.1f}%)",
            "the -50% option stop is advisory at EOD cadence — treat the full debit as "
            "the per-trade risk (already the sizing cap)"))

    # 4) vol-filter blindness: recent options entries mostly IV-unchecked
    recent = sorted(options_entry_actions, key=lambda a: str(a.get("ts", "")))
    if len(recent) >= cfg.unchecked_window:
        win = recent[-cfg.unchecked_window:]
        share = sum(1 for a in win if "IV/HV n/a" in str(a.get("why", ""))) / len(win)
        if share > cfg.unchecked_share:
            flags.append(_flag(
                "vol_unchecked_share", "info",
                f"{share:.0%} of the last {len(win)} options entries were IV-unchecked",
                "the IV/HV filter mostly isn't seeing IV — check chain imp_vol coverage "
                "and the bars fetch"))

    # 5) fresh coordination artifacts -> regression detector for coordination bugs
    fresh = [t for t in excluded
             if _days_ago((t.exit_at_iso or "")[:10], today) <= cfg.artifact_window_days]
    if fresh:
        flags.append(_flag(
            "fresh_artifacts", "info",
            f"{len(fresh)} coordination artifact(s) closed in the last "
            f"{cfg.artifact_window_days} days",
            "sub-hour paper round trips are reappearing — check runner coordination "
            "(sleeve guards / cool-downs)"))
    return flags


def build_scorecard(closed: list[ClosedTrade], actions: list[dict], iso: str,
                    today: str, cfg: ScorecardConfig) -> dict:
    """One weekly scorecard row. Pure: caller supplies trades + action rows + clock."""
    trades = [t for t in closed if t.source in ("paper", "paper_option")]
    excluded = [t for t in trades if analytics.is_coordination_artifact(t)]
    kept = [t for t in trades if not analytics.is_coordination_artifact(t)]
    actions_by_ref, options_entry_days = index_actions(actions)
    trades_by_bucket: dict[str, list[ClosedTrade]] = {}
    for t in kept:
        trades_by_bucket.setdefault(bucket_of(t, actions_by_ref, options_entry_days), []).append(t)
    buckets = {}
    for name, ts_ in sorted(trades_by_bucket.items()):
        b = analytics.stat_block(ts_).model_dump()
        b["building"] = b["trades"] < cfg.min_bucket_n
        buckets[name] = b
    pairs = expression_pairs(trades_by_bucket.get("rsi2-equity", []),
                             trades_by_bucket.get("rsi2-options", []))
    options_entry_actions = [a for a in actions
                             if a.get("source") == "runner:options_entry"
                             and a.get("kind") == "trade"]
    flags = evaluate_rules(buckets, pairs, trades_by_bucket, actions_by_ref,
                           options_entry_actions, excluded, today, cfg)
    return {"ts": iso, "date": today, "buckets": buckets, "pairs": pairs, "flags": flags,
            "excluded_artifacts": len(excluded),
            "sources": {"closed_n": len(kept), "actions_n": len(actions)}}
