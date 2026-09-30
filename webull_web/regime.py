"""The market regime the nightly manager's note and the entry judgment read: SPY vs its 200-day SMA.

Moved here from the retired web app's swing router (2026-09-28, spec systems-only §4): same numbers, same
best-effort contract (every field None when the bars can't be read).
"""
from __future__ import annotations

from webull_api import market_data
from webull_api.strategy.bars import to_ohlcv
from webull_api.strategy.indicators import sma


def swing_regime() -> dict:
    """SPY vs its 200-day SMA. Best-effort: every field None when the bars can't be read."""
    try:
        spy = to_ohlcv(market_data.get_bars("SPY", "D", count="250"))
    except Exception:
        return {"spy_risk_off": None, "spy_price": None, "spy_sma200": None}
    closes = [b["close"] for b in spy]
    s200 = sma(closes, 200)[-1] if closes else None
    price = closes[-1] if closes else None
    risk_off = (price < s200) if (price is not None and s200 is not None) else None
    return {"spy_risk_off": risk_off,
            "spy_price": round(price, 2) if price is not None else None,
            "spy_sma200": round(s200, 2) if s200 is not None else None}
