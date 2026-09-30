"""Code-maintained RSI2 ownership ledger for the REAL account — the lots the real RSI2 runner
opened. Deliberately a separate file from rsi2_store (which is paper-only by contract) so real
and paper lots can never co-mingle.

Written ONLY after a fill is verified at the broker: a queued decision is not a fill, and a
logged intent is not a lot. Reconciled against broker truth every run — drop vanished lots,
adjust changed quantities, NEVER invent one. Attribution has to be earned by this runner, or the
sleeve guard would read a hand-placed position as RSI2-owned."""
from __future__ import annotations

import json
import os
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "rsi2_real_state.json"
_EPS = 1e-6
_PENDING_TTL_DAYS = 5


def _dir() -> Path:
    if os.environ.get("RSI2_REAL_STATE_DIR"):
        return data_dir("activity", "RSI2_REAL_STATE_DIR")
    return data_dir("activity", "ACTIVITY_DIR")


# A pending's decision reached one of these -> it will never fill; free the slot now instead of
# waiting out the 5-day TTL (final-review I1).
_TERMINAL_STATUSES = {"expired", "failed", "cancelled"}


def _default() -> dict:
    return {"schema_version": 1, "owned_lots": [], "pending_orders": [], "exit_rows": {},
            "reconciliation_log": [], "updated_at": None}


def _cost_basis(broker_positions, symbol: str) -> float | None:
    sym = str(symbol).strip().upper()
    for p in broker_positions or []:
        if not isinstance(p, dict):
            continue
        if str(p.get("symbol") or "").strip().upper() != sym:
            continue
        for key in ("cost_price", "costPrice", "cost"):
            try:
                return float(p[key])
            except (KeyError, TypeError, ValueError):
                continue
    return None


def _age_days(queued_date, today: str) -> int:
    from datetime import date
    try:
        a = date.fromisoformat(str(queued_date)[:10])
        b = date.fromisoformat(str(today)[:10])
    except ValueError:
        return 0          # unparseable -> don't expire it on a parse bug
    return (b - a).days


def load() -> dict:
    f = _dir() / _FILE
    if not f.exists():
        return _default()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("owned_lots", [])
    data.setdefault("pending_orders", [])
    data.setdefault("reconciliation_log", [])
    if not isinstance(data.get("exit_rows"), dict):
        data["exit_rows"] = {}
    return data


def save(state: dict, now_iso: str) -> None:
    state["updated_at"] = now_iso
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / _FILE).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _broker_qty(broker_positions) -> dict:
    out: dict[str, float] = {}
    for p in broker_positions or []:
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or "").strip().upper()
        try:
            qty = float(p.get("quantity"))
        except (TypeError, ValueError):
            continue
        if sym:
            out[sym] = out.get(sym, 0.0) + qty
    return out


def _log(state: dict, date: str, action: str, reason: str) -> None:
    state.setdefault("reconciliation_log", []).append(
        {"date": date, "action": action, "reason": reason})


def reconcile(state: dict, broker_positions, today: str) -> tuple[dict, list[str]]:
    """Broker truth wins. Returns (state, human-readable notes)."""
    held = _broker_qty(broker_positions)
    notes: list[str] = []
    kept = []
    for lot in state.get("owned_lots", []):
        sym = str(lot.get("symbol") or "").strip().upper()
        have = held.get(sym)
        if have is None or have <= _EPS:
            note = f"dropped lot {sym} ({lot.get('shares')} sh)"
            notes.append(note)
            _log(state, today, note, "account has no such position — never filled or already closed")
            continue
        if abs(float(lot.get("shares", 0)) - have) > _EPS:
            note = f"adjusted lot {sym} {lot.get('shares')} -> {have} sh"
            notes.append(note)
            _log(state, today, note, "broker quantity differs from the ledger")
            lot = {**lot, "shares": have}
        kept.append(lot)
    state["owned_lots"] = kept
    return state, notes


def record_entry(state: dict, *, symbol, shares, entry_price, entry_date, decision_id) -> dict:
    state.setdefault("owned_lots", []).append(
        {"symbol": str(symbol).strip().upper(), "shares": float(shares),
         "entry_price": float(entry_price), "entry_date": entry_date,
         "decision_id": decision_id})
    return state


def record_pending(state: dict, *, decision_id, symbol, shares, queued_date) -> dict:
    """A queued decision — an INTENT, not a lot. Only adopt_filled can promote it."""
    state.setdefault("pending_orders", []).append(
        {"decision_id": decision_id, "symbol": str(symbol).strip().upper(),
         "shares": float(shares), "queued_date": queued_date})
    return state


def record_exit_row(state: dict, *, symbol, decision_id) -> dict:
    """Remember the id of a standing exit row THIS runner queued.

    The map is the entire authority for what the runner may later cancel. Ids are recorded ONLY
    for rows it appended itself — never for a row merely observed in the queue — because the same
    queue also carries the owner's own hand-queued rsi2_above rows protecting positions this
    ledger does not own. Cancelling by symbol/kind pattern would destroy that protection.
    """
    rows = state.setdefault("exit_rows", {})
    if not isinstance(rows, dict):
        rows = state["exit_rows"] = {}
    rows[str(symbol).strip().upper()] = decision_id
    return state


def drop_exit_row(state: dict, *, symbol) -> dict:
    rows = state.get("exit_rows")
    if isinstance(rows, dict):
        rows.pop(str(symbol).strip().upper(), None)
    return state


def orphan_exit_rows(state: dict) -> list[tuple[str, str]]:
    """(symbol, decision_id) for recorded exit rows whose lot is no longer held. Pure.

    A `qty: ALL` SELL left standing after its lot is gone would liquidate ANY later position in
    that symbol, from any source, the moment RSI(2) crosses the band (final-review C3).
    """
    owned = {str(l.get("symbol") or "").strip().upper() for l in state.get("owned_lots", [])}
    rows = state.get("exit_rows")
    if not isinstance(rows, dict):
        return []
    return [(sym, did) for sym, did in rows.items() if sym not in owned and did]


def prune_pendings(state: dict, status_by_id: dict, today: str) -> tuple[dict, list[str]]:
    """Drop pendings whose queued decision reached a terminal status. Pure.

    A gate-denied / expired / cancelled decision never becomes a fill, but the pending it left
    behind keeps claiming the lot's slot for the whole 5-day TTL — five nights of "no entry" that
    look like signal scarcity in the proof-bar read. A missing or in-flight status keeps the
    pending (adopt_filled's TTL is the backstop): only a KNOWN death frees the slot.
    """
    keep: list[dict] = []
    notes: list[str] = []
    for p in state.get("pending_orders", []):
        status = str((status_by_id or {}).get(p.get("decision_id")) or "").strip().lower()
        if status in _TERMINAL_STATUSES:
            sym = str(p.get("symbol") or "").strip().upper()
            note = f"dropped pending {sym} (decision {status})"
            notes.append(note)
            _log(state, today, note, "the queued decision reached a terminal status")
            continue
        keep.append(p)
    state["pending_orders"] = keep
    return state, notes


def adopt_filled(state: dict, broker_positions, today: str) -> tuple[dict, list[str]]:
    """Promote pendings the broker now actually holds. This is the ONLY way a lot is created.

    Adoption requires a pending row THIS runner wrote, so a hand-placed position is never
    claimed as RSI2-owned — attribution has to be earned, or the sleeve guard would defer a
    position it knows nothing about to an exit rule that does not govern it.

    A pending older than _PENDING_TTL_DAYS is dropped: its decision expired or was refused by
    the gate, and an immortal pending would block the symbol's slot forever.
    """
    held = _broker_qty(broker_positions)
    owned = {str(l.get("symbol") or "").strip().upper() for l in state.get("owned_lots", [])}
    notes: list[str] = []
    keep = []
    for p in state.get("pending_orders", []):
        sym = str(p.get("symbol") or "").strip().upper()
        have = held.get(sym)
        if sym in owned:
            notes.append(f"cleared stale pending {sym} (already an owned lot)")
            continue
        if have is not None and have > _EPS:
            price = _cost_basis(broker_positions, sym)
            state = record_entry(state, symbol=sym, shares=have,
                                 entry_price=price if price is not None else 0.0,
                                 entry_date=today, decision_id=p.get("decision_id"))
            owned.add(sym)
            notes.append(f"adopted {sym} ({have} sh) — fill verified at the broker")
            _log(state, today, f"adopted {sym}", "pending decision verified as a broker position")
            continue
        if _age_days(p.get("queued_date"), today) > _PENDING_TTL_DAYS:
            notes.append(f"expired pending {sym} (never filled)")
            _log(state, today, f"expired pending {sym}", "no matching position within the TTL")
            continue
        keep.append(p)
    state["pending_orders"] = keep
    return state, notes
