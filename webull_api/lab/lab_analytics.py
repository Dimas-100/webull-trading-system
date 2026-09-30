"""Map a trial's forward trades into the unified journal ledger (tagged source='paper_trial') and
summarize the lab's OWN data, REUSING journal.analytics.stat_block/breakdown_by — no reimplementation,
no I/O. The lab summary partitions by strategy / cohort / generation / behavioral_cohort."""
from __future__ import annotations

from collections import Counter

from webull_api.journal.analytics import breakdown_by, stat_block
from webull_api.journal.pairing import _days_between
from webull_api.journal.schema import ClosedTrade
from webull_api.lab.schema import (DEFAULT_GATE_A_CONFIG, LabSummary, StrategyRecord, TrialBook,
                                   TrialPerf)
from webull_api.strategy.schema import EquityPoint, Trade


def trial_trade_to_closed(t: Trade, rec: StrategyRecord, symbol: str) -> ClosedTrade:
    """Map one forward trade into the unified ClosedTrade ledger, stamped with lab provenance so it
    flows through the same win-rate / expectancy / breakdown analytics. Mirrors
    analytics.option_trade_to_closed."""
    return ClosedTrade(
        symbol=symbol, source="paper_trial", quantity=float(t.shares),
        entry_price=t.entry_price, exit_price=t.exit_price,
        entry_at_iso=t.entry_time, exit_at_iso=t.exit_time,
        holding_days=_days_between(t.entry_time, t.exit_time),
        pnl=t.pnl, return_pct=t.return_pct, win=t.pnl > 0,
        setup=rec.strategy.name, instrument="equity",
        strategy_id=rec.id, generation=rec.generation, cohort=rec.cohort,
        behavioral_cohort=rec.behavioral_cohort, trial_id=rec.trial_id)


def normalize_curve(curve: list[EquityPoint], starting_equity: float) -> list[EquityPoint]:
    """Raw $ -> %-of-start (equity/starting_equity*100) so trials are directly comparable."""
    base = starting_equity or 1.0
    return [EquityPoint(time=p.time, equity=p.equity / base * 100.0) for p in curve]


def _closed_for(rec: StrategyRecord, book: TrialBook) -> list[ClosedTrade]:
    return [trial_trade_to_closed(t, rec, sym)
            for sym, trades in book.trades_by_symbol.items() for t in trades]


def build_lab_summary(records: list[StrategyRecord], books: dict[str, TrialBook], *,
                      generated_at_iso: str = "") -> LabSummary:
    """Flatten all trials' trades into tagged ClosedTrades; overall = stat_block(all); the four
    breakdowns = breakdown_by(...); per-record TrialPerf. Empty -> zeroed, no crash."""
    closed: list[ClosedTrade] = []
    trials: list[TrialPerf] = []
    for r in records:
        book = books.get(r.trial_id) if r.trial_id else None
        if book is None:
            continue
        rec_closed = _closed_for(r, book)
        closed.extend(rec_closed)
        trials.append(TrialPerf(
            id=r.id, name=r.strategy.name, status=r.status, stats=stat_block(rec_closed),
            equity_curve=normalize_curve(book.equity_curve, book.starting_equity),
            score=(r.score.score if r.score else None),
            forward_trades=book.forward_trades, forward_bars=book.forward_bars))
    return LabSummary(
        generated_at_iso=generated_at_iso,
        overall=stat_block(closed),
        by_strategy=breakdown_by(closed, lambda t: t.strategy_id or "?"),
        by_cohort=breakdown_by(closed, lambda t: t.cohort or "?"),
        by_generation=breakdown_by(closed, lambda t: str(t.generation) if t.generation is not None else "?"),
        by_behavioral_cohort=breakdown_by(closed, lambda t: t.behavioral_cohort or "?"),
        trials=trials)


def live_vs_proven_rows(proven, closed: list[ClosedTrade]) -> list[dict]:
    """Loop seam #2 surface: per proven strategy, its LIVE attributed-paper performance (from the
    `closed` trades tagged with the strategy's trial/name) next to its Gate-B (SIMULATED) forward
    proof expectancy, with a `holding` / `decayed` / `no_live_data` read. Pure. `proven` items need
    `.strategy.name`, `.gate_b` (a dict), `.graduated_at`. 'holding' = the live per-trade expectancy
    is at least half the proof's (>= 0 when the proof is non-positive); anything less with >=1 live
    trade is 'decayed'. The proof is an UPPER BOUND (single stress-cost sim), so this is the honest
    'did the edge survive real fills?' read — observability only; nothing is demoted automatically."""
    rows: list[dict] = []
    for p in proven:
        tid = str(p.gate_b.get("trial_id") or p.strategy.name)
        name = p.strategy.name
        proof_exp = float((p.gate_b.get("live_metrics") or {}).get("expectancy", 0.0) or 0.0)
        mine = [c for c in closed if (c.trial_id == tid or c.strategy_id == name)]
        s = stat_block(mine)
        if s.trades == 0:
            verdict = "no_live_data"
        elif s.expectancy >= (0.5 * proof_exp if proof_exp > 0 else 0.0):
            verdict = "holding"
        else:
            verdict = "decayed"
        rows.append({
            "name": name, "trial_id": tid, "graduated_at": p.graduated_at,
            "proof_expectancy": proof_exp,
            "live": {"trades": s.trades, "expectancy": s.expectancy,
                     "win_rate": s.win_rate, "total_pnl": s.total_pnl},
            "verdict": verdict,
        })
    return rows


# Owner-facing labels for Gate-A fail codes (SSOT — the web panel and the manager's note both
# render these). Unknown codes fall back to the raw code so a new gate never crashes a surface.
FAIL_CODE_LABELS: dict[str, str] = {
    "ci_lb_negative": "no provable edge (CI lower bound ≤ 0)",
    "edge_below_floor": "edge below the complexity hurdle",
    "breadth_fail": "doesn't generalize across the basket",
    "dsr_below_floor": "deflated Sharpe below the floor",
    "pf_below_floor": "profit factor below 1",
    "insufficient_sample": "fires too rarely (small trade sample)",
    "dd_breach": "drawdown breach",
    "insufficient_regime_coverage": "not enough market regimes covered",
    "degenerate_no_exit": "exit rule never fires",
    "daily_only_v1": "non-daily timeframe (v1 screens daily only)",
    "insufficient_history": "not enough price history to screen",
    "cost_fragile": "edge dies under stressed costs",
    "param_fragile": "edge doesn't survive parameter nudges",
}


_NEAR_MISS_METRICS = ("dsr", "ci_lb", "net_edge", "edge_floor", "breadth_frac",
                      "pooled_trades", "max_drawdown_pct")


def _gate_a_fails(cycle: dict) -> list[dict]:
    return [f for f in (cycle.get("gate_a_failed") or []) if isinstance(f, dict)]


def funnel_summary(cycles: list[dict], *, proven_total: int = 0, recent: int = 12) -> dict:
    """Gate-A funnel + fail-code aggregates from parsed cycles.jsonl dicts (oldest→newest).
    Pure — the caller provides the input. `rows` is windowed to the last `recent` cycles;
    `lifetime` and `fail_codes[].lifetime` span ALL cycles passed in. `near_misses` are
    candidates that failed only 1–2 gates (newest cycles first, capped at 10) — the
    'closest to passing' signal that says where the search should push next."""
    rows = [{
        "cycle_seq": c.get("cycle_seq"), "date": c.get("cycle_date"),
        "generated": c.get("generated", 0) or 0, "screened": c.get("screened", 0) or 0,
        "duplicates": c.get("duplicates", 0) or 0,
        "accepted": len(c.get("accepted") or []), "new_proving": len(c.get("new_proving") or []),
        "promoted": len(c.get("promoted") or []), "killed": len(c.get("killed") or []),
    } for c in cycles[-max(recent, 1):]] if cycles else []

    lifetime_codes: Counter = Counter()
    for c in cycles:
        for f in _gate_a_fails(c):
            lifetime_codes.update(f.get("fail_codes") or [])

    latest = cycles[-1] if cycles else None
    latest_codes: Counter = Counter()
    if latest:
        for f in _gate_a_fails(latest):
            latest_codes.update(f.get("fail_codes") or [])

    # Margin-aware ranking (2026-08-15): fewest fail codes first, then smallest DSR shortfall
    # (the dominant blocker and the one metric every candidate has), newest cycle breaking ties.
    # Entries persisted before metrics existed carry no `dsr` and sort last within their group.
    near: list[dict] = []
    for c in cycles:
        for f in _gate_a_fails(c):
            codes = f.get("fail_codes") or []
            if 1 <= len(codes) <= 2:
                entry = {"fingerprint": f.get("fingerprint", "?"),
                         "fail_codes": list(codes), "cycle_seq": c.get("cycle_seq")}
                entry.update({k: f[k] for k in _NEAR_MISS_METRICS if f.get(k) is not None})
                near.append(entry)

    def _near_key(n: dict) -> tuple:
        dsr = n.get("dsr")
        gap = (DEFAULT_GATE_A_CONFIG.dsr_min - dsr) if isinstance(dsr, (int, float)) \
            else float("inf")
        return (len(n["fail_codes"]), gap, -(n.get("cycle_seq") or 0))
    near.sort(key=_near_key)

    fail_codes = [{"code": code, "label": FAIL_CODE_LABELS.get(code, code),
                   "lifetime": n, "latest": latest_codes.get(code, 0)}
                  for code, n in lifetime_codes.most_common()]
    return {
        "rows": rows,
        "lifetime": {"cycles_run": len(cycles),
                     "generated": sum(c.get("generated", 0) or 0 for c in cycles),
                     "screened": sum(c.get("screened", 0) or 0 for c in cycles),
                     "accepted": sum(len(c.get("accepted") or []) for c in cycles),
                     "proven": proven_total},
        "fail_codes": fail_codes,
        "top_blockers": [f["label"] for f in fail_codes[:2]],
        "near_misses": near[:10],
        "dsr_floor": DEFAULT_GATE_A_CONFIG.dsr_min,
        "latest": ({"cycle_seq": latest.get("cycle_seq"), "date": latest.get("cycle_date"),
                    "screened": latest.get("screened", 0) or 0,
                    "accepted": len(latest.get("accepted") or [])} if latest else None),
    }
