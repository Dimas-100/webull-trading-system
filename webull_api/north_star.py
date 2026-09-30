"""Pure North Star scorecard engine. Dict-in / dict-out; no I/O, no market data, no orders.

Assembles the three-pillar program status: (1) Strategy Lab, (2) paper trading, (3) real-trading
go-live readiness. All thresholds live here so the logic that gates real money is unit-tested."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

# function imports: the module-level `scorecard(...)` composer below shadows the module name
from webull_api.scorecard import bucket_of as _bucket_of, index_actions as _index_actions

# Amended 2026-08-03 (ledger rsi2-live-stay-paper): owner funding target ~$1.5k; the original
# $400 was superseded by the 2026-07-28 reachability finding and must not light the check.
FUNDING_TARGET: float = 1500.0
DECISIONS_TARGET: int = 30

# proof-bar-read-scope (ledger 2026-07-28): the funding-gate bar counts only rsi2-equity rows
# whose ENTRY is at/after the 2026-07-24 17:30 ET coordination fix.
PROOF_BAR_ENTRY_CUTOFF = datetime(2026, 7, 24, 17, 30, tzinfo=ZoneInfo("America/New_York"))


def proof_bar_sample(closed_equity: list, actions: list[dict]) -> tuple[list, dict]:
    """Split closed EQUITY round-trips into (bar_rows, context_counts) per the ledger decision
    `proof-bar-read-scope`: the bar counts ONLY rsi2-equity rows (entry fill attributed to
    runner:rsi2 — the same scorecard.bucket_of read the Friday eval uses) entered at/after
    PROOF_BAR_ENTRY_CUTOFF. Legacy/unattributed equity, proven auto-trades, and pre-fix rsi2
    rows are counted as context, never as the bar. Fail-closed: a row whose entry timestamp
    will not parse cannot prove it is post-fix, so it drops to context; a naive timestamp is
    read as ET. The options sleeve never enters here — the caller counts it as context."""
    by_ref, entry_days = _index_actions(actions)
    kept: list = []
    ctx = {"pre_fix_rsi2": 0, "legacy_equity": 0, "proven_auto": 0, "unparseable_entry": 0}
    for t in closed_equity:
        bucket = _bucket_of(t, by_ref, entry_days)
        if bucket.startswith("proven:"):
            ctx["proven_auto"] += 1
        elif bucket != "rsi2-equity":
            ctx["legacy_equity"] += 1
        else:
            try:
                entry = datetime.fromisoformat(t.entry_at_iso)
            except (ValueError, TypeError):
                ctx["unparseable_entry"] += 1
                continue
            if entry.tzinfo is None:
                entry = entry.replace(tzinfo=PROOF_BAR_ENTRY_CUTOFF.tzinfo)
            if entry >= PROOF_BAR_ENTRY_CUTOFF:
                kept.append(t)
            else:
                ctx["pre_fix_rsi2"] += 1
    return kept, ctx

_PREREQ_KEYS: tuple[str, ...] = ("funded", "proof_bar", "review")
_REAL_ITEMS: list[tuple[str, str]] = [
    ("funded", f"Account funded (${int(FUNDING_TARGET)})"),
    ("proof_bar", f"Proof bar ({DECISIONS_TARGET} decisions, +edge)"),
    ("review", "Security review + sign-off"),
    ("armed", "Autopilot armed"),
]


def lab_block(lab: dict) -> dict:
    if lab.get("unavailable"):
        return {"chip": "unknown", "proven": 0, "proven_names": [], "in_flight": 0,
                "cycles_run": 0, "last_cycle_date": "", "stale": False}
    proven = int(lab.get("proven", 0))
    stale = bool(lab.get("stale", False))
    chip = "producing" if proven > 0 else ("stale" if stale else "testing")
    return {"proven": proven, "proven_names": list(lab.get("proven_names", [])),
            "in_flight": int(lab.get("in_flight", 0)), "cycles_run": int(lab.get("cycles_run", 0)),
            "last_cycle_date": lab.get("last_cycle_date", "") or "", "stale": stale, "chip": chip}


def paper_block(paper: dict) -> dict:
    if paper.get("unavailable"):
        return {"chip": "unknown", "equity_realized_pnl": 0.0, "options_realized_pnl": 0.0,
                "combined_realized_pnl": 0.0, "open_positions": 0, "decisions": 0,
                "decisions_target": DECISIONS_TARGET, "expectancy": 0.0, "expectancy_pct": 0.0,
                "excluded_artifacts": 0, "win_rate": 0.0, "context": {}}
    eq = float(paper.get("equity_realized_pnl", 0.0))
    op = float(paper.get("options_realized_pnl", 0.0))
    return {"equity_realized_pnl": eq, "options_realized_pnl": op, "combined_realized_pnl": eq + op,
            "open_positions": int(paper.get("open_positions", 0)),
            "decisions": int(paper.get("decisions", 0)), "decisions_target": DECISIONS_TARGET,
            "expectancy": float(paper.get("expectancy", 0.0)),
            "expectancy_pct": float(paper.get("expectancy_pct", 0.0)),
            "excluded_artifacts": int(paper.get("excluded_artifacts", 0)),
            "win_rate": float(paper.get("win_rate", 0.0)),
            "context": dict(paper.get("context", {}) or {}), "chip": "active"}


def proof_bar(sample: int, expectancy: float, win_rate: float, target: int = DECISIONS_TARGET,
              expectancy_pct: float | None = None) -> dict:
    sample_met = sample >= target
    # The edge must hold in the size-independent unit too: a mixed-lot sample whose $ and %
    # expectancy disagree in sign is a red flag, not a pass. pct omitted -> dollar-only
    # (compat for callers that don't compute %).
    edge_met = expectancy > 0 and (expectancy_pct is None or expectancy_pct > 0)
    return {"sample": sample, "sample_target": target, "sample_met": sample_met,
            "expectancy": expectancy, "expectancy_pct": expectancy_pct, "edge_met": edge_met,
            "win_rate": win_rate, "machine_met": sample_met and edge_met}


def readiness(*, funded: bool | None, machine_met: bool, reviewed: bool, enabled: bool,
              kill_active: bool, ever_placed: bool, caps: dict | None = None) -> dict:
    funded_ok = funded is True
    cleared = funded_ok and machine_met and reviewed
    if not enabled:
        armed_state = "pending"
    elif kill_active:
        armed_state = "attention"          # armed but kill-switch engaged
    elif cleared:
        armed_state = "done"               # properly armed and cleared to trade
    else:
        armed_state = "attention"          # HOT before the gate is complete (the safety flag)
    states = {
        "funded": "unknown" if funded is None else ("done" if funded_ok else "pending"),
        "proof_bar": "done" if machine_met else "pending",
        "review": "done" if reviewed else "pending",
        "armed": armed_state,
    }
    checklist = [{"key": k, "label": lbl, "state": states[k]} for k, lbl in _REAL_ITEMS]
    blockers = sum(1 for p in (funded_ok, machine_met, reviewed) if not p)
    return {"checklist": checklist, "caps": caps or {}, "enabled": enabled,
            "kill_active": kill_active, "ever_placed": ever_placed, "cleared_to_go_live": cleared,
            "safety_flag": enabled and not cleared, "blockers_remaining": blockers,
            "chip": "live" if ever_placed else "not_live"}


def readiness_unavailable() -> dict:
    """Degraded real pillar: autopilot/config state could not be read. Everything unknown; never
    asserts a go-live gate is met and never raises the safety flag from missing data."""
    checklist = [{"key": k, "label": lbl, "state": "unknown"} for k, lbl in _REAL_ITEMS]
    return {"checklist": checklist, "caps": {}, "enabled": False, "kill_active": False,
            "ever_placed": False, "cleared_to_go_live": False, "safety_flag": False,
            "blockers_remaining": len(_PREREQ_KEYS), "chip": "unknown"}


def scorecard(*, lab: dict, paper: dict, real: dict) -> dict:
    """Assemble the three already-built pillar blocks into the API response."""
    return {"lab": lab, "paper": paper, "real": real}
