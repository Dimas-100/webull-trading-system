"""Autopilot configuration — loaded from env. ALL OFF / strict by default. Pure."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from . import paths


def _f(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)
    return v if math.isfinite(v) else float(default)


def _i(name: str, default: int) -> int:
    try:
        v = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)
    if not math.isfinite(v):
        return int(default)
    try:
        return int(v)
    except (OverflowError, ValueError):
        return int(default)


def _b(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AutopilotConfig:
    enabled: bool = False
    max_notional: float = 40.0
    max_positions: int = 1
    max_orders_per_day: int = 5
    daily_loss_halt: float = 40.0          # halt new BUYs once day P/L <= -this
    max_positions_risk_off: int = 0
    kill_file: str = field(default_factory=paths.default_kill_file)  # absolute, <repo>/autopilot/KILL
    windows: str = "17:00-20:00"
    notify: str = ""
    # The owner's phone topic (the same ntfy topic every other push uses). A run that placed a
    # real order or errored sends a short note there (2026-09-21); "" = no push.
    push_url: str = ""
    # Decision executor (2026-08-07 unattended-execution spec). OFF by default; every field is an
    # owner-set env — a session can queue decisions but can never raise these.
    decisions_enabled: bool = False
    max_decisions_per_day: int = 3
    opt_max_debit: float = 70.0    # dollars of premium per option decision (defined risk)
    opt_max_open: int = 2          # max open option units the executor may accumulate
    opt_max_debit_frac_of_width: float = 0.65  # vertical EDGE filter: share of the width paid
    cooling_minutes: int = 120     # risk-adding (BUY) decisions must age this long before acting

    @classmethod
    def from_env(cls) -> "AutopilotConfig":
        return cls(
            enabled=_b("WEBULL_AUTOPILOT_ENABLED"),
            max_notional=_f("WEBULL_AUTOPILOT_MAX_NOTIONAL", 40.0),
            max_positions=_i("WEBULL_AUTOPILOT_MAX_POSITIONS", 1),
            max_orders_per_day=_i("WEBULL_AUTOPILOT_MAX_ORDERS_PER_DAY", 5),
            daily_loss_halt=_f("WEBULL_AUTOPILOT_DAILY_LOSS_HALT", 40.0),
            max_positions_risk_off=_i("WEBULL_AUTOPILOT_MAX_POSITIONS_RISK_OFF", 0),
            kill_file=paths.resolve_kill_file(os.environ.get("WEBULL_AUTOPILOT_KILL_FILE")),
            windows=os.environ.get("WEBULL_AUTOPILOT_WINDOWS", "17:00-20:00"),
            notify=os.environ.get("WEBULL_AUTOPILOT_NOTIFY", ""),
            push_url=os.environ.get("WEBULL_MANAGER_NOTE_NTFY", "").strip(),
            decisions_enabled=_b("WEBULL_AUTOPILOT_DECISIONS_ENABLED"),
            max_decisions_per_day=_i("WEBULL_AUTOPILOT_MAX_DECISIONS_PER_DAY", 3),
            opt_max_debit=_f("WEBULL_OPTIONS_MAX_DEBIT", 70.0),
            opt_max_open=_i("WEBULL_OPTIONS_MAX_OPEN", 2),
            opt_max_debit_frac_of_width=_f("WEBULL_OPTIONS_MAX_DEBIT_FRAC_OF_WIDTH", 0.65),
            cooling_minutes=_i("WEBULL_AUTOPILOT_COOLING_MINUTES", 120),
        )
