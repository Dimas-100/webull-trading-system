"""Compose the North Star scorecard from on-disk state. Reuses lab_service, the paper stores,
journal analytics, and the autopilot config. NO broker/market-data call, NO trading import."""
from __future__ import annotations

import logging
import os

from webull_api import action_log, north_star
from webull_api.autopilot import audit
from webull_api.autopilot import config as ap_config
from webull_api.autopilot import paths as ap_paths
from webull_api.journal import analytics, pairing

from . import journal_store, lab_service, paper_options_store, paper_store

_log = logging.getLogger(__name__)


def _qty(pos: dict) -> float:
    try:
        return float(pos.get("qty", pos.get("quantity", 0)) or 0)
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _positions_list(store: dict) -> list:
    """Paper accounts store positions either as a {symbol: {...}} dict (equity) or a [{...}] list
    (options). Normalize to a list of position dicts so the open-position count works for both."""
    pos = store.get("positions") or []
    if isinstance(pos, dict):
        return list(pos.values())
    return list(pos)


def _lab_inputs() -> dict:
    try:
        sv = lab_service.status_view()
        shelf = sv.get("proven_shelf", {}) or {}
        by_status = (sv.get("library", {}) or {}).get("by_status", {}) or {}
        in_flight = sum(int(v) for k, v in by_status.items()
                        if k in ("proving", "provisional_proven"))
        return {"proven": shelf.get("total", 0),
                "proven_names": [r.get("name") for r in shelf.get("records", [])],
                "in_flight": in_flight, "cycles_run": sv.get("cycles_run", 0),
                "last_cycle_date": sv.get("last_cycle_date", ""), "stale": sv.get("stale", False)}
    except Exception:
        _log.exception("north_star: lab inputs unavailable")
        return {"unavailable": True}


def _paper_inputs() -> dict:
    try:
        eq = paper_store.load() or {}
        op = paper_options_store.load() or {}
        eq_pnl = float(eq.get("realized_pnl", 0.0) or 0.0)
        op_pnl = float(op.get("realized_pnl", 0.0) or 0.0)
        open_pos = sum(1 for p in _positions_list(eq) if _qty(p) > 0) \
            + sum(1 for p in _positions_list(op) if _qty(p) != 0)
        closed_eq, _open = pairing.pair_fills(journal_store.load_fills())
        # proof-bar-read-scope (ledger 2026-07-28), code-enforced: the bar counts ONLY post-fix
        # rsi2-equity rows; legacy/unattributed equity, proven auto-trades, pre-fix rows, and the
        # ENTIRE options sleeve are context counts — reported, never pooled into the sample.
        bar_rows, ctx = north_star.proof_bar_sample(closed_eq, action_log.load())
        # `note`-marked OptionTrades are manually-reconstructed historical rows (e.g. the
        # 2026-08-15 META backfill) — Journal-only, excluded even from the context count.
        options_n = sum(1 for t in journal_store.load_option_trades() if not t.note)
        # Runner-coordination artifacts (sub-hour paper round trips) are not decisions; they
        # drop out of the gate read but stay in the Journal — and the count is shown, never
        # silent.
        kept = [c for c in bar_rows if not analytics.is_coordination_artifact(c)]
        stats = analytics.stat_block(kept)
        return {"equity_realized_pnl": eq_pnl, "options_realized_pnl": op_pnl,
                "open_positions": open_pos, "decisions": stats.trades,
                "excluded_artifacts": len(bar_rows) - len(kept),
                "context": {**ctx, "options": options_n},
                "expectancy": stats.expectancy, "expectancy_pct": stats.expectancy_pct,
                "win_rate": stats.win_rate}
    except Exception:
        _log.exception("north_star: paper inputs unavailable")
        return {"unavailable": True}


def _real_inputs(net_liq: float | None) -> dict:
    try:
        cfg = ap_config.AutopilotConfig.from_env()
        funded = None if net_liq is None else (float(net_liq) >= north_star.FUNDING_TARGET)
        kill_active = os.path.exists(cfg.kill_file)
        reviewed = (ap_paths.autopilot_dir() / "GO_LIVE_APPROVED").exists()
        ever_placed = audit.placed_count() > 0
        caps = {"max_notional": cfg.max_notional, "max_positions": cfg.max_positions,
                "max_orders_per_day": cfg.max_orders_per_day, "daily_loss_halt": cfg.daily_loss_halt,
                "max_positions_risk_off": cfg.max_positions_risk_off, "windows": cfg.windows}
        return {"funded": funded, "reviewed": reviewed, "enabled": cfg.enabled,
                "kill_active": kill_active, "ever_placed": ever_placed, "caps": caps}
    except Exception:
        _log.exception("north_star: real inputs unavailable")
        return {"unavailable": True}


def build(net_liq: float | None = None) -> dict:
    lab_in = _lab_inputs()
    paper_in = _paper_inputs()
    real_in = _real_inputs(net_liq)
    lab = north_star.lab_block(lab_in)
    paper = north_star.paper_block(paper_in)
    if real_in.get("unavailable"):
        real = north_star.readiness_unavailable()
    else:
        pb = north_star.proof_bar(paper_in.get("decisions", 0), paper_in.get("expectancy", 0.0),
                                  paper_in.get("win_rate", 0.0),
                                  expectancy_pct=paper_in.get("expectancy_pct"))
        real = north_star.readiness(funded=real_in["funded"], machine_met=pb["machine_met"],
                                    reviewed=real_in["reviewed"], enabled=real_in["enabled"],
                                    kill_active=real_in["kill_active"],
                                    ever_placed=real_in["ever_placed"], caps=real_in["caps"])
    return north_star.scorecard(lab=lab, paper=paper, real=real)
