"""Pure candlestick + pullback detection for the swing planner (Section 4–5 of the plan)."""
from __future__ import annotations


def reversal_trigger(bars: list[dict]) -> dict | None:
    """On the LAST completed candle: a bullish reversal at/above support, or None.

    Returns {"kind": "close_above_prior_high"|"engulfing"|"hammer", "trigger_high": float}.
    """
    if len(bars) < 2:
        return None
    cur, prev = bars[-1], bars[-2]
    o, h, l, c = cur["open"], cur["high"], cur["low"], cur["close"]
    po, pc, ph = prev["open"], prev["close"], prev["high"]
    if None in (o, h, l, c, po, pc, ph) or h <= l:
        return None
    mid = (h + l) / 2
    upper_half = c >= mid

    # 1) Closes above the prior day's high (strongest, simplest).
    if c > ph:
        return {"kind": "close_above_prior_high", "trigger_high": h}
    # 2) Bullish engulfing closing in the upper half.
    if c > o and c >= po and o <= pc and upper_half:
        return {"kind": "engulfing", "trigger_high": h}
    # 3) Hammer: long lower wick, small body near the top, close in the upper half.
    body = abs(c - o)
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)
    if lower_wick >= 2 * max(body, 1e-9) and upper_wick <= max(body, 1e-9) and upper_half:
        return {"kind": "hammer", "trigger_high": h}
    return None


def pullback_swing_low(bars: list[dict], window: int = 10) -> float | None:
    """Lowest low from the most recent swing-high pivot (left=right=3) to the end;
    falls back to the lowest low of the last `window` completed bars."""
    if not bars:
        return None
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    n = len(bars)
    pivot_idx = None
    for i in range(n - 4, 2, -1):  # most recent detectable swing high (needs 3 bars to its right)
        w = highs[i - 3:i + 4]
        if len(w) == 7 and None not in w and highs[i] == max(w):
            pivot_idx = i
            break
    start = pivot_idx if pivot_idx is not None else max(0, n - window)
    seg = [x for x in lows[start:] if x is not None]
    return min(seg) if seg else None
