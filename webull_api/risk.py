"""Pure price-based risk metrics over chronological closes (oldest-first), + an impure
`risk_for` over Webull bars. Reuses momentum.daily_returns / strategy.indicators.atr /
options_analytics.DEFAULT_RISK_FREE. No Finnhub; no network in the pure functions."""
from __future__ import annotations

import math
import statistics

from .momentum import daily_returns
from .options_analytics import DEFAULT_RISK_FREE
from .strategy.indicators import atr


def annualized_volatility(closes: list[float], ppy: int = 252) -> float | None:
    r = daily_returns(closes)
    if len(r) < 2:
        return None
    return statistics.stdev(r) * math.sqrt(ppy) * 100


def max_drawdown(closes: list[float]) -> float | None:
    vals = [c for c in closes if c is not None]
    if not vals:
        return None
    peak = vals[0]
    mdd = 0.0
    for c in vals:
        if c > peak:
            peak = c
        if peak:
            dd = (c / peak - 1) * 100
            if dd < mdd:
                mdd = dd
    return round(mdd, 2)


def _ann_return_and_vol(closes: list[float], ppy: int):
    r = daily_returns(closes)
    if len(r) < 2:
        return None, None
    return statistics.mean(r) * ppy, statistics.stdev(r) * math.sqrt(ppy)


def sharpe(closes: list[float], rf: float = DEFAULT_RISK_FREE, ppy: int = 252) -> float | None:
    ann_ret, ann_vol = _ann_return_and_vol(closes, ppy)
    if ann_ret is None or not ann_vol:
        return None
    return (ann_ret - rf) / ann_vol


def sortino(closes: list[float], rf: float = DEFAULT_RISK_FREE, ppy: int = 252) -> float | None:
    r = daily_returns(closes)
    if len(r) < 2:
        return None
    ann_ret = statistics.mean(r) * ppy
    downside = [x for x in r if x < 0]
    if not downside:
        return None
    dd = math.sqrt(sum(x * x for x in downside) / len(r)) * math.sqrt(ppy)
    if dd == 0:
        return None
    return (ann_ret - rf) / dd


def atr_pct(highs, lows, closes, period: int = 14) -> float | None:
    a = atr(highs, lows, closes, period)
    last_atr = a[-1] if a else None
    last = closes[-1] if closes else None
    if last_atr is None or not last:
        return None
    return last_atr / last * 100


def risk_label(vol_pct: float | None) -> str:
    if vol_pct is None:
        return "unknown"
    if vol_pct < 20:
        return "low"
    if vol_pct <= 40:
        return "moderate"
    return "high"


def _drawdown_series(closes: list[float]) -> list[float]:
    """Per-bar drawdown as a non-positive pct from the running peak."""
    vals = [c for c in closes if c is not None]
    out: list[float] = []
    peak = float("-inf")
    for c in vals:
        peak = max(peak, c)
        out.append((c / peak - 1) * 100 if peak > 0 else 0.0)
    return out


def ulcer_index(closes: list[float]) -> float | None:
    """Root-mean-square of the drawdown series (Martin ratio's denominator). 0 = never below
    a prior peak. None when there are no usable closes."""
    dd = _drawdown_series(closes)
    if not dd:
        return None
    return math.sqrt(sum(d * d for d in dd) / len(dd))


def conditional_drawdown(closes: list[float], q: float = 0.95) -> float | None:
    """CDaR: mean of the worst (1-q) tail of the drawdown series (a negative pct). A stable
    alternative to the single max-DD extreme. None when there are no usable closes."""
    dd = _drawdown_series(closes)
    if not dd:
        return None
    ordered = sorted(dd)                       # most-negative first
    tail = max(1, int(round((1 - q) * len(ordered))))
    worst = ordered[:tail]
    return sum(worst) / len(worst)


# ── impure entry point (Webull bars) ─────────────────────────────────────────────

from .market_data import get_bars  # noqa: E402
from .strategy.bars import to_ohlcv  # noqa: E402


def _r2(v):
    return None if v is None else round(v, 2)


def risk_for(symbol: str) -> dict:
    """Fetch Webull daily bars and compute the risk bundle. Entitlement -> 402 (propagates)."""
    bars = to_ohlcv(get_bars(symbol, count="300"))
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    vol = annualized_volatility(closes)
    return {
        "symbol": symbol.upper(),
        "last": closes[-1] if closes else None,
        "volatility": _r2(vol),
        "max_drawdown": max_drawdown(closes),
        "sharpe": _r2(sharpe(closes)),
        "sortino": _r2(sortino(closes)),
        "atr_pct": _r2(atr_pct(highs, lows, closes)),
        "risk_label": risk_label(vol),
    }
