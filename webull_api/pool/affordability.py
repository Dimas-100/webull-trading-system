"""Pure affordability math for the real-book sizing rule (spec 2026-09-11 pool-shadow design §3):
whole shares at equity/divisor per symbol, and a summary grid across equities and divisors. No I/O
-- the caller (`scripts/sizing_guidance.py`) supplies prices from wherever it likes (Tiingo store
last closes, in practice). Mirrors the (archived 2026-09-29) pool ledger's whole-share floor exactly, including
its epsilon guard (`EPS`): decimal arithmetic done in binary occasionally undercounts a share that
divides evenly (20.70 / 6.90 == 2.9999999999999996, not 3)."""
from __future__ import annotations

import math

EPS = 1e-9


def affordable(prices: dict[str, float], equity: float, divisor: int) -> dict[str, int]:
    """Whole shares per symbol at ``floor((equity / divisor) / price)``. A symbol with no usable
    price (missing, None, zero or negative) or that affords zero shares is excluded entirely --
    skip, never stretch (spec §3): there is no such thing as a "0 shares" row."""
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    slot_size = equity / divisor
    out: dict[str, int] = {}
    for symbol, price in prices.items():
        if price is None or price <= 0:
            continue
        shares = math.floor(slot_size / price + EPS)
        if shares >= 1:
            out[symbol] = shares
    return out


def table(prices: dict[str, float], equities: list[float], divisors: tuple[int, ...] = (6, 4)) -> list[dict]:
    """One row per equity: at each divisor, the slot size, how many of the priced symbols one
    share fits at that slot, and which ones. ``total`` is the count of usably-priced symbols (an
    unpriced name can be judged neither affordable nor not), the same denominator at every row so
    rows compare directly (e.g. "8/28")."""
    total = sum(1 for p in prices.values() if p is not None and p > 0)
    rows: list[dict] = []
    for eq in equities:
        row: dict = {"equity": eq, "total": total, "by_divisor": {}}
        for d in divisors:
            afford = affordable(prices, eq, d)
            row["by_divisor"][d] = {"slot": eq / d, "affordable": len(afford), "symbols": sorted(afford)}
        rows.append(row)
    return rows
