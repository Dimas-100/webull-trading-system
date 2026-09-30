"""Autopilot daily state + kill-switch. Fail-closed on the kill read. File-backed."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from webull_web import store_io

from .paths import autopilot_dir


def _dir() -> Path:
    return autopilot_dir()


def kill_switch_active(kill_file: str) -> bool:
    """True if the kill-file exists OR its state can't be determined (fail-closed)."""
    try:
        return Path(kill_file).exists()
    except Exception:
        return True  # in doubt, halt


@dataclass
class DailyState:
    day: str
    orders_today: int = 0
    realized_loss: float = 0.0
    halt_tripped: bool = False
    placed_symbols: list = field(default_factory=list)


def _path(day: str) -> Path:
    return _dir() / "state" / f"{day}.json"


def load_state(day: str) -> DailyState:
    f = _path(day)
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d.pop("day", None)
            return DailyState(day=day, **d)
        except Exception:
            pass
    return DailyState(day=day)


def save_state(s: DailyState) -> None:
    p = _path(s.day)
    p.parent.mkdir(parents=True, exist_ok=True)
    store_io.atomic_write_json(p, {
        "day": s.day, "orders_today": s.orders_today, "realized_loss": s.realized_loss,
        "halt_tripped": s.halt_tripped, "placed_symbols": list(s.placed_symbols),
    }, indent=2)


def _first_seen_path() -> Path:
    return _dir() / "state" / "decisions_first_seen.json"


def load_first_seen() -> dict:
    """Executor-OWNED first-observation stamps for queued decisions (decision id -> ISO time).
    Lives in the autopilot store, NOT the writer-owned queue file, so no queue writer can forge
    the cooling basis (final-review finding I2 residual). Unreadable -> {} (cooling then restarts
    from now — the fail-closed direction)."""
    f = _first_seen_path()
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {str(k): str(v) for k, v in d.items()}
        except Exception:
            pass
    return {}


def save_first_seen(m: dict) -> None:
    p = _first_seen_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    store_io.atomic_write_json(p, dict(m), indent=2)


def _confirmed_path() -> Path:
    return _dir() / "state" / "decisions_confirmed.json"


def load_confirmed() -> dict:
    """Executor-OWNED rsi2_above confirmations (decision key -> "YYYY-MM-DD" close date).

    Written by a post-close run whose fresh RSI(2) crossed the band; consumed by the next
    core-hours run's MARKET submit (2026-08-18 two-phase exit spec — evening MARKET is
    417-rejected, so the pass that can SEE the signal and the pass that can ACT are split).
    Keyed like cooling (id + writer-fields hash) so a rewritten queue row cannot inherit one.
    Unreadable -> {} (no execution until the next post-close run re-confirms — fail-closed)."""
    f = _confirmed_path()
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {str(k): str(v) for k, v in d.items()}
        except Exception:
            pass
    return {}


def save_confirmed(m: dict) -> None:
    p = _confirmed_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    store_io.atomic_write_json(p, dict(m), indent=2)


def _acted_path() -> Path:
    return _dir() / "state" / "decisions_acted.json"


def load_acted() -> dict:
    """Executor-OWNED consumed-decision tokens (decision id -> "<iso>|<ASSET>" for attempted
    submits; "deny:<iso>"/"failed:<iso>"/"expired:<iso>" for non-submit outcomes). An id with a
    non-deny token has been consumed; a queue row re-presenting it as 'queued' is a REPLAY and is
    refused. This file is also the SOLE source for the decisions/day counter and the open-option-
    units cap — unreadable -> {} means those caps reset (the replay wall still has the queue's
    status marks as a second layer, the caps do NOT; the state dir is the trust root)."""
    f = _acted_path()
    if f.exists():
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(d, dict):
                return {str(k): str(v) for k, v in d.items()}
        except Exception:
            pass
    return {}


def save_acted(m: dict) -> None:
    p = _acted_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    store_io.atomic_write_json(p, dict(m), indent=2)
