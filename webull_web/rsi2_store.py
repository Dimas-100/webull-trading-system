"""Code-maintained RSI2 ownership ledger — the lots the RSI2 paper runner owns. Written ONLY by the
runner, ONLY after a fill is verified in the paper account (fixing 'a logged intent is not a fill').
Under $RSI2_STATE_DIR or $ACTIVITY_DIR (default data/activity), gitignored. Reconciled against the authoritative account
every run: drop vanished lots, adjust changed quantities, never invent a lot."""
from __future__ import annotations

import json
import os
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "rsi2_state.json"
_EPS = 1e-6


def _dir() -> Path:
    if os.environ.get("RSI2_STATE_DIR"):
        return data_dir("activity", "RSI2_STATE_DIR")
    return data_dir("activity", "ACTIVITY_DIR")


def _default() -> dict:
    return {"schema_version": 1, "owned_lots": [], "pending_orders": [],
            "reconciliation_log": [], "updated_at": None}


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
    return data


def save(state: dict, now_iso: str) -> None:
    state["updated_at"] = now_iso
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / _FILE).write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _positions(acct) -> dict:
    """symbol -> quantity from a PaperAccount (or a dict-shaped account)."""
    pos = getattr(acct, "positions", None)
    if pos is None and isinstance(acct, dict):
        pos = acct.get("positions", {})
    out = {}
    for sym, p in (pos or {}).items():
        q = getattr(p, "quantity", None)
        if q is None and isinstance(p, dict):
            q = p.get("quantity")
        out[sym] = float(q or 0.0)
    return out


def reconcile(state: dict, acct, today: str) -> tuple[dict, list]:
    positions = _positions(acct)
    kept, notes = [], []
    for lot in state.get("owned_lots", []):
        sym = lot.get("symbol")
        held = positions.get(sym, 0.0)
        want = float(lot.get("shares") or 0.0)
        if held <= _EPS:
            notes.append({"date": today, "action": f"dropped lot {sym} ({want:g} sh)",
                          "reason": f"account has no {sym} position — logged lot never filled or already closed"})
            continue
        if held < want - _EPS:
            # Position shrank below what we recorded — clamp DOWN so RSI2 never tries to sell
            # more than exists. We never adjust UP: extra shares in the account are foreign
            # (manual holds / other routines) and must stay invisible to RSI2's exits.
            notes.append({"date": today, "action": f"adjusted lot {sym} {want:g} -> {held:g} sh",
                          "reason": f"account quantity ({held:g}) is below the ledger ({want:g})"})
            lot["shares"] = held
        kept.append(lot)
    state["owned_lots"] = kept
    if notes:
        state.setdefault("reconciliation_log", []).extend(notes)
    return state, notes


def record_entry(state: dict, *, symbol, shares, entry_price, entry_date, paper_order_id) -> dict:
    state.setdefault("owned_lots", []).append(
        {"symbol": symbol, "shares": float(shares), "entry_price": float(entry_price),
         "entry_date": entry_date, "paper_order_id": paper_order_id})
    return state


def record_exit(state: dict, symbol: str) -> dict:
    state["owned_lots"] = [l for l in state.get("owned_lots", []) if l.get("symbol") != symbol]
    return state


def record_pending(state: dict, *, paper_order_id, symbol, side, placed_date) -> dict:
    """A queued next-open order this runner placed. NOT a lot: owned_lots stays fills-only —
    adoption promotes/drops pendings once the order reaches a terminal status."""
    state.setdefault("pending_orders", []).append(
        {"paper_order_id": paper_order_id, "symbol": symbol, "side": side,
         "placed_date": placed_date})
    return state


def adopt_settled(state: dict, orders_by_id: dict, today: str) -> tuple[dict, list]:
    """Promote pendings whose order reached a terminal status. orders_by_id: paper_order_id ->
    PaperOrder from the AUTHORITATIVE account (history + open orders), so adoption is crash-
    and rerun-safe: a settle that happened in a run that later died is adopted here. Filled BUY
    -> lot at the actual open; filled SELL -> lot removed; rejected/cancelled/expired OR a
    vanished id -> pending dropped with a note; still-pending -> kept."""
    keep, notes = [], []
    for p in state.get("pending_orders", []):
        o = orders_by_id.get(p.get("paper_order_id"))
        status = getattr(o, "status", None)
        if status == "pending":
            keep.append(p)
            continue
        if status == "filled" and p.get("side") == "BUY":
            record_entry(state, symbol=p["symbol"], shares=float(o.quantity),
                         entry_price=float(o.fill_price), entry_date=(o.fill_session or today),
                         paper_order_id=p["paper_order_id"])
            notes.append({"date": today, "action": f"adopted BUY {p['symbol']} @ {o.fill_price:g}",
                          "reason": f"settled at session {o.fill_session} open"})
        elif status == "filled":
            record_exit(state, p["symbol"])
            notes.append({"date": today, "action": f"adopted SELL {p['symbol']}",
                          "reason": f"settled at session {o.fill_session} open"})
        else:
            notes.append({"date": today,
                          "action": f"dropped pending {p.get('side')} {p.get('symbol')}",
                          "reason": f"order ended '{status or 'missing'}' — never filled"})
    state["pending_orders"] = keep
    if notes:
        state.setdefault("reconciliation_log", []).extend(notes)
    return state, notes
