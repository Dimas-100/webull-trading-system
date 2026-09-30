"""Owner cash-flow auto-detection — the suite step right after the net-liq snapshot.
Webull's OpenAPI has no transfers endpoint, so deposits/withdrawals are INFERRED from the
managed book's own records: flow = Δcash between the last two cash-bearing snapshots
+ buy notional − sell notional of the fills inside that window. For a CASH account this
is an exact identity — a fill moves cash by exactly its notional, so whatever remains is
external money (dividends/interest land here too; the entry note says "auto-detected" so
the owner can amend). Appends the flow to the contributions ledger, which feeds the P/L
headline, the deposit marks, and the deposit-adjusted growth index. Reads only
trading.get_order_history (the journal_ingest precedent) — never the order gate; the
only write is the contributions ledger."""
from __future__ import annotations

import logging
from datetime import datetime

from webull_api import run_log, trading
from webull_api.journal.normalize import fills_from_order_history

from . import contributions_store, netliq_snapshot_service, netliq_store, paper_service, runner_util

_log = logging.getLogger(__name__)
_KEY = "flows"
_AUTO_TAG = "auto-detected"


def _net_traded(aid: str, start_iso: str, end_iso: str, history_fn) -> float:
    """Signed cash the window's fills consumed: +buy notional − sell notional, for fills
    strictly after `start_iso` up to and including `end_iso`. Raises on an unparseable
    fill stamp — a fill we can't place in time would silently corrupt the flow."""
    lo, hi = datetime.fromisoformat(start_iso), datetime.fromisoformat(end_iso)
    net = 0.0
    for f in fills_from_order_history(history_fn(aid), aid):
        at = datetime.fromisoformat(f.filled_at_iso)
        if lo < at <= hi:
            net += f.quantity * f.price if f.side == "BUY" else -(f.quantity * f.price)
    return net


def _already_recorded(entries: list[dict], today: str) -> bool:
    return any(str(e.get("date")) == today and _AUTO_TAG in str(e.get("note", "")) for e in entries)


def run(force: bool = False, *, history_fn=None, account_fn=None) -> dict:
    iso, today = paper_service.now_et()
    with runner_util.filelock(_KEY, iso) as got:
        if not got:
            return {"result": "no_op", "errors": [],
                    "summary": "Flows: another run in progress", "ran_at": iso}
        if runner_util.already_ran_today(_KEY, today, force):
            return {"result": "no_op", "errors": [],
                    "summary": "Flows: already ran today", "ran_at": iso}

        # The two cash-bearing snapshots that bound the detection window. No baseline yet
        # (or tonight's snapshot failed) is an EXPECTED state, not an error: report ok with
        # a run-log stamp so the watchdog doesn't page over a still-accumulating trail.
        rows = [r for r in netliq_store.load() if r.get("real_cash") is not None]
        cur = next((r for r in reversed(rows) if str(r.get("date")) == today), None)
        prev = next((r for r in reversed(rows) if str(r.get("date")) < today), None)
        if cur is None or prev is None:
            summary = "Flows: need two cash-bearing snapshots (baseline still accumulating)"
            run_log.append([{"key": _KEY, "ts": iso, "result": "ok", "summary": summary,
                             "placed": 0, "errors": []}])
            return {"result": "ok", "errors": [], "summary": summary, "ran_at": iso, "entry": None}

        entry = None
        try:
            if _already_recorded(contributions_store.load(), today):
                result, summary = "ok", "Flows: today's flow already recorded"
            else:
                aid = account_fn() if account_fn else netliq_snapshot_service.best_real_account()[0]
                if not aid:
                    raise RuntimeError("managed account unresolvable")
                net = _net_traded(str(aid), str(prev["ts"]), str(cur["ts"]),
                                  history_fn or trading.get_order_history)
                dcash = float(cur["real_cash"]) - float(prev["real_cash"])
                flow = round(dcash + net, 2)
                if abs(flow) < 0.01:
                    result, summary = "ok", "Flows: no external flow detected"
                else:
                    entry = contributions_store.append({
                        "date": today, "amount": flow,
                        "note": f"{_AUTO_TAG} external flow (Δcash {dcash:+.2f}, net traded {net:+.2f})",
                        "created_at": iso,
                    })
                    result, summary = "ok", f"Flows: recorded {flow:+.2f} owner flow"
        except Exception as e:
            _log.exception("flows: detection failed")
            result, summary = "error", f"Flows: detection failed — {e}"

        run_log.append([{"key": _KEY, "ts": iso, "result": result, "summary": summary,
                         "placed": 0, "errors": [] if result == "ok" else [summary]}])
        return {"result": result, "errors": [] if result == "ok" else [summary],
                "summary": summary, "ran_at": iso, "entry": entry}
