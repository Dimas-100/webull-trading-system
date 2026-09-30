"""Attribution rows for the REAL RSI2 sleeve's lots (2026-09-18).

The scorecard buckets a closed trade by the action row whose `ref` is the ENTRY fill's journal
id (`scorecard.bucket_of`), and the evening note carries an action row's `why` (the web
Overview, which read a real fill with no matching `sleeve: real` row as a manual, owner-placed
fill, and the Activity feed were archived with the web app, 2026-09-28). The real runner never
wrote such rows, so every system entry read as "manual fill" with no why, and every real round
trip bucketed `unattributed-equity`. This module writes exactly
one row per adopted lot, keyed by the lot's journal fill, so the sleeve's own trades bucket as
`rsi2-real` (never `rsi2-equity` — the proof bar reads that paper bucket only; the weekly
scorecard itself is paper-only, so the bucket is a hook for a future real-book read, not a
scorecard change today).

Pure matching, fail-safe: a lot with zero or several candidate fills gets NO row (a wrong
attribution is worse than a missing one); a fill an earlier lot already claimed is not a
candidate (a re-entry in the same name within the window); fills of other accounts are ignored
when the account is known. Idempotent by the lot's decision id (row id = the fill id, so
action_log.append dedups too). Reads the journal and the action log; never touches the queue or
an order path.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import rsi2_real_store

_ET = ZoneInfo("America/New_York")
SOURCE = "runner:rsi2_real"
SLEEVE = "real"          # the sleeve value this module tags its own action rows with
WINDOW_DAYS = rsi2_real_store._PENDING_TTL_DAYS   # a fill is adopted within the pending TTL


def row_id(fill_id: str) -> str:
    return f"rsi2-real:{fill_id}"


def _et(stamp) -> datetime | None:
    """Fill stamp -> aware ET datetime. Naive stamps are ET (the repo convention); None if bad."""
    try:
        dt = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_ET)
    return dt.astimezone(_ET)


def rows_for(owned_lots, fills, existing_rows, *, account_id: str | None = None,
             window_days: int = WINDOW_DAYS) -> tuple[list[dict], list[str]]:
    """(action rows to append, human notes). Pure.

    A lot's fill is the ONE real BUY of its symbol (in `account_id`, when given) dated (ET)
    within `window_days` before its `entry_date` up to that date inclusive — adoption happens
    at the 17:30 run of the fill day, or a later day if that run was missed, never before the
    fill — that no earlier lot's row already claims. `existing_rows` are this runner's rows
    already on disk (source == SOURCE).
    """
    ours = [r for r in existing_rows or [] if isinstance(r, dict) and r.get("source") == SOURCE]
    done_decisions = {r.get("decision_id") for r in ours if r.get("decision_id")}
    claimed_fills = {r.get("ref") for r in ours if r.get("ref")}
    rows: list[dict] = []
    notes: list[str] = []
    for lot in owned_lots or []:
        sym = str(lot.get("symbol") or "").strip().upper()
        decision_id = lot.get("decision_id")
        if decision_id and decision_id in done_decisions:
            continue
        try:
            entry = date.fromisoformat(str(lot.get("entry_date"))[:10])
        except (TypeError, ValueError):
            notes.append(f"unattributed {sym}: unparseable entry_date {lot.get('entry_date')!r}")
            continue
        lo = entry - timedelta(days=window_days)
        cands: list[tuple[datetime, object]] = []
        for f in fills or []:
            if getattr(f, "source", None) != "real" or getattr(f, "side", None) != "BUY":
                continue
            if str(getattr(f, "symbol", "") or "").strip().upper() != sym:
                continue
            if account_id and str(getattr(f, "account_id", "") or "") != str(account_id):
                continue
            if getattr(f, "id", None) in claimed_fills:
                continue
            at = _et(getattr(f, "filled_at_iso", None))
            if at is None or not (lo <= at.date() <= entry):
                continue
            cands.append((at, f))
        if not cands:
            # A lot without a decision id can only be recognised by its fill; when that fill is
            # already claimed the lot IS attributed — stay silent rather than nag nightly.
            if not decision_id and any(
                    getattr(f, "id", None) in claimed_fills
                    and str(getattr(f, "symbol", "") or "").strip().upper() == sym
                    for f in fills or []):
                continue
            notes.append(f"unattributed {sym}: no matching real BUY fill")
            continue
        if len(cands) > 1:
            notes.append(f"unattributed {sym}: {len(cands)} candidate fills (ambiguous)")
            continue
        at, f = cands[0]
        rows.append({
            "id": row_id(f.id), "ref": f.id, "ts": at.isoformat(timespec="microseconds"),
            "kind": "trade", "sleeve": SLEEVE, "symbol": sym, "side": "BUY",
            "qty": float(f.quantity), "decision_id": decision_id,
            "account_id": getattr(f, "account_id", None),
            "why": (f"rsi2-real entry — lot adopted by the real RSI2 ledger "
                    f"(decision {decision_id}); attribution row, not a placement"),
            "source": SOURCE,
        })
        claimed_fills.add(f.id)
        notes.append(f"attributed {sym} -> fill {f.id}")
    return rows, notes


def record(state: dict, *, account_id: str | None = None, fills_fn=None, existing_fn=None,
           append_fn=None) -> list[str]:
    """Write the missing attribution rows for `state['owned_lots']`. Returns the notes."""
    from webull_api import action_log

    from . import journal_store
    fills = (fills_fn or journal_store.load_fills)()
    existing = (existing_fn or action_log.load)()
    rows, notes = rows_for(state.get("owned_lots", []), fills, existing, account_id=account_id)
    if rows:
        (append_fn or action_log.append)(rows)
    return notes
