"""Position sizing for the Webull swing book (Section 6 of swing-trading-plan.md).

Pure, deterministic. Whole shares only. 1% target risk, 2% hard ceiling, 0.5% floor,
40% single-name notional cap. Returns a viable size or ok=False with the blocking reason.
"""
from __future__ import annotations

import math

RISK_PCT = 1.0        # target risk as % of BOOK
RISK_CEIL_PCT = 2.0   # hard ceiling
RISK_FLOOR_PCT = 0.5  # below this is noise
NOTIONAL_CAP_PCT = 40.0  # max single-position notional as % of BOOK


def size_position(book: float, entry: float, stop: float) -> dict:
    per_share = entry - stop

    def fail(reason: str) -> dict:
        return {"shares": 0, "actual_risk_dollars": 0.0, "actual_risk_pct": 0.0,
                "notional": 0.0, "ok": False, "reason": reason}

    if per_share <= 0 or book <= 0 or entry <= 0:
        return fail("invalid entry/stop")

    risk_dollars = book * RISK_PCT / 100
    notional_cap = book * NOTIONAL_CAP_PCT / 100
    max_shares_notional = math.floor(notional_cap / entry)
    shares = math.floor(risk_dollars / per_share)

    if shares == 0:
        one_risk_pct = per_share / book * 100
        if one_risk_pct <= RISK_CEIL_PCT and max_shares_notional >= 1:
            shares = 1
        else:
            return fail(f"1 share risks {one_risk_pct:.1f}% (>2% ceiling) or exceeds 40% notional")

    # Never let a single name exceed the notional cap.
    shares = min(shares, max_shares_notional)
    if shares < 1:
        return fail("entry too large for the 40% notional cap")

    # Size a trivially-small position up toward the 2% ceiling, bounded by the notional cap.
    actual_pct = shares * per_share / book * 100
    if actual_pct < RISK_FLOOR_PCT:
        target = min(math.floor(book * RISK_CEIL_PCT / 100 / per_share), max_shares_notional)
        if target >= 1:
            shares = max(shares, target)
        actual_pct = shares * per_share / book * 100
        if actual_pct < RISK_FLOOR_PCT:
            return fail("sub-0.5% risk; notional cap prevents sizing up (noise)")

    notional = shares * entry
    actual_risk = shares * per_share
    actual_pct = actual_risk / book * 100
    if not (RISK_FLOOR_PCT <= actual_pct <= RISK_CEIL_PCT and shares >= 1 and notional <= notional_cap + 1e-9):
        return fail(f"no viable whole-share size (risk {actual_pct:.2f}%, notional {notional:.0f})")
    return {"shares": shares, "actual_risk_dollars": round(actual_risk, 2),
            "actual_risk_pct": round(actual_pct, 4), "notional": round(notional, 2),
            "ok": True, "reason": ""}
