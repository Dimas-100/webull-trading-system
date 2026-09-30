"""Trend x volatility regime bucketing over a chronological bar slice. Pure; degrades on short
slices (uses what's available; side/low when undefined). Mirrors the swing-plan regime idea but
returns the lab schema's RegimeTag."""
from __future__ import annotations

import math
import statistics

from .schema import RegimeTag


def compute_regime(bars: list[dict], *, sma_period: int = 200, vol_window: int = 20,
                   ppy: int = 252, high_vol_pct: float = 25.0) -> RegimeTag:
    closes = [b["close"] for b in bars if b.get("close") is not None]
    if len(closes) < 2:
        return RegimeTag(trend="side", vol="low")

    # ── trend: last close vs SMA + SMA direction over two same-length, NON-OVERLAPPING blocks ──
    # p_eff caps the period at half the slice so sma_now and sma_prev are ALWAYS same-period means
    # of adjacent blocks. (The old `half = min(p, len//2)` made sma_prev a different-period mean on
    # slices shorter than 2*sma_period — a perfect uptrend 1..100 classified "side", garbling every
    # Gate-A fold's trend label at the default sma_period=200 over ~90-130-bar folds.)
    p_eff = max(1, min(sma_period, len(closes) // 2))
    sma_now = sum(closes[-p_eff:]) / p_eff
    sma_prev = sum(closes[-2 * p_eff:-p_eff]) / p_eff
    last = closes[-1]
    if last > sma_now and sma_now >= sma_prev:
        trend = "up"
    elif last < sma_now and sma_now <= sma_prev:
        trend = "down"
    else:
        trend = "side"

    # ── vol: annualized realized vol of recent daily returns ──
    w = min(vol_window, len(closes) - 1)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(len(closes) - w, len(closes))
            if closes[i - 1]]
    if len(rets) < 2:
        vol = "low"
    else:
        ann = statistics.pstdev(rets) * math.sqrt(ppy) * 100
        vol = "high" if ann >= high_vol_pct else "low"

    return RegimeTag(trend=trend, vol=vol)
