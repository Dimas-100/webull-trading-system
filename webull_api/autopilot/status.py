"""Read-only autopilot status for the Monitor.

Composes the live config, the daily state (kill-switch / loss-halt / order count), and TODAY's
audit log (`autopilot/log/<day>.jsonl`) into one snapshot — so the Monitor can answer "is the
real-money surface active as intended, and safe?" at a glance, and both the Monitor panel and the
cadence row read the SAME computed state (no divergence).

Strictly read-only: NO broker/market-data call, NO trading import, NO placement. Every function
tolerates a missing/blank/corrupt store and `snapshot()` never raises — observability must never
be able to affect the placement path. This does NOT gate anything; the fail-closed gate lives in
`gate.py`/`run.py` and is untouched.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import audit
from . import config as ap_config
from . import state as state_mod
from .paths import autopilot_dir


def _classify(entry: dict) -> str:
    """A single audit line -> one of placed / errored / skipped / other. Mirrors run.py's writes:
    a place that threw carries a non-empty ``error``; a real fill carries ``placed is True``; a
    gate denial carries ``allow is False``. Order matters (an errored place has placed=False)."""
    if entry.get("error"):
        return "errored"
    if entry.get("placed") is True:
        return "placed"
    if entry.get("allow") is False:
        return "skipped"
    return "other"


def today_decisions(day: str) -> dict:
    """Parse `autopilot/log/<day>.jsonl` into placed/skipped/errored counts, skip reasons grouped
    by (reason, layer), and placement errors. Tolerates absent file / blank / corrupt / non-dict
    lines. ``last_decision_ts`` = the log file's mtime (audit lines carry no per-line timestamp;
    the orchestrator is the real-money path and is deliberately not touched for observability)."""
    path = autopilot_dir() / "log" / f"{day}.jsonl"
    out = {
        "day": day, "placed": 0, "skipped": 0, "errored": 0, "decisions": 0,
        "last_decision_ts": None, "skips": [], "errors": [], "placed_orders": [],
    }
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    try:
        out["last_decision_ts"] = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        pass
    skips: dict[tuple, int] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        out["decisions"] += 1
        cls = _classify(entry)
        if cls == "placed":
            out["placed"] += 1
            out["placed_orders"].append({"symbol": entry.get("symbol"), "side": entry.get("side"),
                                         "source": entry.get("source")})
        elif cls == "errored":
            out["errored"] += 1
            out["errors"].append({"symbol": entry.get("symbol"),
                                  "error": str(entry.get("error"))[:160]})
        elif cls == "skipped":
            out["skipped"] += 1
            key = (str(entry.get("reason") or "?"), str(entry.get("layer") or "?"))
            skips[key] = skips.get(key, 0) + 1
    out["skips"] = [{"reason": r, "layer": lyr, "count": n}
                    for (r, lyr), n in sorted(skips.items(), key=lambda kv: (-kv[1], kv[0]))]
    return out


def _posture(*, enabled: bool, kill_active: bool, halt_tripped: bool) -> str:
    """The one-word live posture (drives the Monitor badge). kill takes precedence over loss-halt
    (both stop placement, but the kill-switch is the owner's explicit dead-man stop)."""
    if not enabled:
        return "off"
    if kill_active:
        return "halted"
    if halt_tripped:
        return "loss_halt"
    return "armed"


_ET = ZoneInfo("America/New_York")


def default_day(now: datetime | None = None) -> str:
    """The snapshot's day when the caller gives none: the EASTERN date. The runner keys its state
    and audit files by the local (ET) date; a UTC default (the 2026-09-04 cockpit-shell follow-up)
    rolled to tomorrow at 20:00 ET, so every evening note, strip and report built after that hour
    read an empty day — no run, zero orders — while the 17:45 run had happened."""
    return (now or datetime.now(timezone.utc)).astimezone(_ET).date().isoformat()


def snapshot(day: str | None = None, *, cfg=None) -> dict:
    """Full read-only autopilot status for the Monitor. Returns ``{"unavailable": True}`` only if
    the config itself can't be read; otherwise never raises. ``armed_warning`` is the danger signal
    the owner wants surfaced: the gate is hot (`WEBULL_AUTOPILOT_ENABLED`), nothing is halting it,
    and it has not been formally cleared to go live — i.e. real orders can place unattended."""
    try:
        cfg = cfg or ap_config.AutopilotConfig.from_env()
    except Exception:
        return {"unavailable": True}
    day = day or default_day()
    try:
        kill_active = state_mod.kill_switch_active(cfg.kill_file)
    except Exception:
        kill_active = True  # fail-closed: in doubt, show HALTED rather than a false "live"
    try:
        st = state_mod.load_state(day)
        halt_tripped, orders_today = bool(st.halt_tripped), int(st.orders_today)
        realized_loss = float(st.realized_loss)  # today's realized LOSS (≤0), folded into the halt
    except Exception:
        halt_tripped, orders_today, realized_loss = False, 0, 0.0
    try:
        go_live_approved = (autopilot_dir() / "GO_LIVE_APPROVED").exists()
    except Exception:
        go_live_approved = False
    try:
        placed_total = audit.placed_count()
    except Exception:
        placed_total = 0
    posture = _posture(enabled=cfg.enabled, kill_active=kill_active, halt_tripped=halt_tripped)
    return {
        "enabled": cfg.enabled,
        "kill_active": kill_active,
        "halt_tripped": halt_tripped,
        "go_live_approved": go_live_approved,
        "orders_today": orders_today,
        "realized_loss": realized_loss,
        "posture": posture,
        "armed_warning": bool(cfg.enabled and not kill_active and not go_live_approved),
        "windows": cfg.windows,
        "caps": {
            "max_notional": cfg.max_notional, "max_positions": cfg.max_positions,
            "max_orders_per_day": cfg.max_orders_per_day, "daily_loss_halt": cfg.daily_loss_halt,
            "max_positions_risk_off": cfg.max_positions_risk_off,
        },
        "today": today_decisions(day),
        "placed_total": placed_total,
    }
