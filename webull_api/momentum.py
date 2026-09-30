"""Pure relative-strength & momentum math over chronological OHLCV bars (oldest-first).

No network in the pure functions; `momentum_for` is the only impure entry point and uses
Webull bars only (benchmarks are passed in by the caller), keeping this module Webull-pure.
"""
from __future__ import annotations

WINDOWS = {"1M": 21, "3M": 63, "6M": 126, "12M": 252}


def returns_over(closes: list[float], windows: dict[str, int] = WINDOWS) -> dict[str, float | None]:
    """Simple return (percent) over each window's bar count; None when too little history."""
    last = closes[-1] if closes else None
    out: dict[str, float | None] = {}
    for name, n in windows.items():
        if last is None or len(closes) <= n or not closes[-1 - n]:
            out[name] = None
        else:
            out[name] = (last / closes[-1 - n] - 1) * 100
    return out


def align_by_date(stock_bars: list[dict], bench_bars: list[dict]) -> tuple[list[float], list[float]]:
    """Intersect two bar lists on the 'time' field -> aligned close series (oldest-first)."""
    bench_by_t = {b.get("time"): b.get("close") for b in bench_bars if b.get("time")}
    s, c = [], []
    for b in stock_bars:
        t = b.get("time")
        if t and t in bench_by_t:
            s.append(b.get("close"))
            c.append(bench_by_t[t])
    return s, c


def daily_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1]:
            out.append(closes[i] / closes[i - 1] - 1)
    return out


def _returns_pairwise(a: list[float], b: list[float]) -> tuple[list[float], list[float]]:
    ra, rb = [], []
    for i in range(1, min(len(a), len(b))):
        if a[i - 1] and b[i - 1]:
            ra.append(a[i] / a[i - 1] - 1)
            rb.append(b[i] / b[i - 1] - 1)
    return ra, rb


def beta(stock_closes: list[float], bench_closes: list[float]) -> float | None:
    """cov(stock,bench)/var(bench) over aligned daily returns; None if undefined."""
    ra, rb = _returns_pairwise(stock_closes, bench_closes)
    n = len(rb)
    if n < 2:
        return None
    mb = sum(rb) / n
    var = sum((x - mb) ** 2 for x in rb) / n
    if var == 0:
        return None
    ma = sum(ra) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / n
    return cov / var


def range_position(price, hi, lo) -> float | None:
    if price is None or hi is None or lo is None or hi == lo:
        return None
    return (price - lo) / (hi - lo) * 100


def relative_strength(stock_closes, bench_closes, windows: dict[str, int] = WINDOWS) -> dict:
    """Per-window excess return (stock - bench, percent) + leader/laggard/mixed label."""
    sret = returns_over(stock_closes, windows)
    bret = returns_over(bench_closes, windows)
    excess: dict[str, float | None] = {}
    wins = losses = total = 0
    for name in windows:
        s, b = sret[name], bret[name]
        if s is None or b is None:
            excess[name] = None
        else:
            excess[name] = s - b
            total += 1
            if s - b > 0:
                wins += 1
            elif s - b < 0:
                losses += 1
    if total == 0:
        label = "unknown"
    elif wins > losses:
        label = "leader"
    elif losses > wins:
        label = "laggard"
    else:
        label = "mixed"
    return {"excess": excess, "label": label}


def momentum_score(excess_by_window: dict) -> dict:
    """Transparent average of available window excess returns (vs SPY), with a band label."""
    vals = [v for v in excess_by_window.values() if v is not None]
    if not vals:
        return {"score": None, "label": "unknown", "basis": []}
    score = round(sum(vals) / len(vals), 2)
    label = "strong" if score > 3 else "weak" if score < -3 else "neutral"
    return {"score": score, "label": label, "basis": [round(v, 2) for v in vals]}


# ── impure entry point (Webull bars only; benchmarks supplied by the caller) ──────

from .market_data import get_bars  # noqa: E402
from .strategy.bars import to_ohlcv  # noqa: E402


def momentum_for(symbol: str, benchmarks: dict[str, str]) -> dict:
    """Fetch Webull daily bars for the symbol + each benchmark and compute the momentum bundle.

    `benchmarks` maps a display label to a symbol; include "SPY" for beta + the momentum score.
    MarketDataNotEntitledError from the symbol's bars propagates (-> 402); a benchmark whose
    bars fail degrades that benchmark to {"error": "unavailable"} without failing the rest.
    """
    bars = to_ohlcv(get_bars(symbol, count="300"))
    closes = [b["close"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]
    last = closes[-1] if closes else None
    hi52 = max(highs[-252:]) if highs else None
    lo52 = min(lows[-252:]) if lows else None
    out = {
        "symbol": symbol.upper(),
        "last": last,
        "returns": returns_over(closes),
        "range_position": range_position(last, hi52, lo52),
        "high_52w": hi52,
        "low_52w": lo52,
        "dist_from_high_pct": None if (last is None or not hi52) else (last / hi52 - 1) * 100,
        "relative_strength": {},
        "beta": None,
    }
    spy_excess: dict = {}
    for label, bsym in benchmarks.items():
        try:
            bbars = to_ohlcv(get_bars(bsym, count="300"))
        except Exception:
            out["relative_strength"][label] = {"symbol": bsym.upper(), "error": "unavailable"}
            continue
        s_al, b_al = align_by_date(bars, bbars)
        rs = relative_strength(s_al, b_al)
        out["relative_strength"][label] = {"symbol": bsym.upper(), **rs}
        if label == "SPY":
            spy_excess = rs["excess"]
            out["beta"] = beta(s_al, b_al)
    out["momentum"] = momentum_score(spy_excess)
    return out
