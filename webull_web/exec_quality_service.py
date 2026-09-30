"""Execution-quality view — exec_ledger decisions joined with REAL journal fills, derived
fresh on every read (nothing persisted). Read-only; never imports trading."""
from __future__ import annotations

from webull_api import exec_ledger, exec_quality

from . import journal_store


def view() -> dict:
    decisions = exec_ledger.load()
    fills = [f.model_dump() for f in journal_store.load_fills() if f.source == "real"]
    rows = exec_quality.slippage_rows(decisions, fills)
    matched = {r["id"] for r in rows}
    awaiting = sum(1 for d in decisions if d.get("id") not in matched)
    return {"summary": exec_quality.summary(rows, awaiting=awaiting), "rows": rows[-50:]}
