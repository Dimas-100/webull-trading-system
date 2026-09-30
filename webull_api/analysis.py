"""Pure analysis over chronological OHLCV bars: a technicals bundle + swing-pivot support/resistance.
Read-only math; reuses the strategy indicators. No network."""
from __future__ import annotations

from .strategy.indicators import ema, rolling_high, rolling_low, rsi, sma


def _last(series):
    return series[-1] if series else None


def _pct(closes, n):
    if len(closes) <= n or closes[-1] is None or not closes[-1 - n]:
        return None
    return (closes[-1] / closes[-1 - n] - 1) * 100


def technicals(bars: list[dict]) -> dict:
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    price = _last(closes)
    s20, s50 = _last(sma(closes, 20)), _last(sma(closes, 50))
    trend = "sideways"
    if price is not None and s20 is not None and s50 is not None:
        if price > s20 > s50:
            trend = "uptrend"
        elif price < s20 < s50:
            trend = "downtrend"
    return {
        "price": price,
        "sma20": s20,
        "sma50": s50,
        "ema20": _last(ema(closes, 20)),
        "rsi14": _last(rsi(closes, 14)),
        "high20": _last(rolling_high(highs, 20)),
        "low20": _last(rolling_low(lows, 20)),
        "pct_change_1": _pct(closes, 1),
        "pct_change_5": _pct(closes, 5),
        "pct_change_20": _pct(closes, 20),
        "trend": trend,
    }


def _pivots(values, left, right, kind):
    """Prices where values[i] is the max ('high') or min ('low') of [i-left, i+right] (edges skipped)."""
    out = []
    for i in range(left, len(values) - right):
        window = values[i - left:i + right + 1]
        if any(v is None for v in window):
            continue
        if (kind == "high" and values[i] == max(window)) or (kind == "low" and values[i] == min(window)):
            out.append(values[i])
    return out


def _cluster(levels, cluster_pct):
    """Merge sorted levels within cluster_pct% of the running cluster mean."""
    out = []
    for lv in sorted(levels):
        if out and out[-1]["mean"] and abs(lv - out[-1]["mean"]) / out[-1]["mean"] * 100 <= cluster_pct:
            g = out[-1]
            g["sum"] += lv
            g["n"] += 1
            g["mean"] = g["sum"] / g["n"]
        else:
            out.append({"sum": lv, "n": 1, "mean": lv})
    return [round(g["mean"], 2) for g in out]


def support_resistance(bars: list[dict], left=3, right=3, cluster_pct=1.0, max_levels=3) -> dict:
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    closes = [b["close"] for b in bars]
    price = closes[-1] if closes else None
    if price is None:
        return {"price": None, "support": [], "resistance": []}
    levels = _cluster(_pivots(highs, left, right, "high") + _pivots(lows, left, right, "low"), cluster_pct)
    support = sorted([x for x in levels if x < price], reverse=True)[:max_levels]
    resistance = sorted([x for x in levels if x > price])[:max_levels]
    return {"price": round(price, 2), "support": support, "resistance": resistance}
