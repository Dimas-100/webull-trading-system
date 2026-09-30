"""Pure technical indicators over price lists. Each returns a list aligned to the input,
with None during the warmup region or on a data gap. Float-only; no numpy dependency."""
from __future__ import annotations


def sma(values, period):
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(len(values)):
        if i < period - 1:
            continue
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = sum(window) / period
    return out


def ema(values, period):
    out = [None] * len(values)
    if period <= 0:
        return out
    k = 2.0 / (period + 1)
    prev = None
    for i, v in enumerate(values):
        if v is None:
            prev = None
            continue
        if prev is None:
            window = values[i - period + 1:i + 1] if i >= period - 1 else None
            if window and all(x is not None for x in window):
                prev = sum(window) / period
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(values, period):
    out = [None] * len(values)
    if period <= 0 or len(values) <= period:
        return out
    avg_gain = avg_loss = None
    for i in range(1, len(values)):
        if values[i] is None or values[i - 1] is None:
            avg_gain = avg_loss = None
            continue
        change = values[i] - values[i - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        if avg_gain is None:
            if i >= period and all(values[j] is not None for j in range(i - period, i + 1)):
                gains = [max(values[j] - values[j - 1], 0.0) for j in range(i - period + 1, i + 1)]
                losses = [max(values[j - 1] - values[j], 0.0) for j in range(i - period + 1, i + 1)]
                avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
            else:
                continue
        else:
            avg_gain = (avg_gain * (period - 1) + gain) / period
            avg_loss = (avg_loss * (period - 1) + loss) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - 100.0 / (1.0 + rs)
    return out


def rolling_high(values, period):
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(len(values)):
        if i < period - 1:
            continue
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = max(window)
    return out


def rolling_low(values, period):
    out = [None] * len(values)
    if period <= 0:
        return out
    for i in range(len(values)):
        if i < period - 1:
            continue
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        out[i] = min(window)
    return out


def atr(highs, lows, closes, period):
    """Average True Range (Wilder), aligned to the input; None during warmup / on a gap."""
    n = len(closes)
    out = [None] * n
    if period <= 0 or n <= period:
        return out
    tr = [None] * n
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if None in (h, l, pc):
            continue
        tr[i] = max(h - l, abs(h - pc), abs(l - pc))
    prev = None
    for i in range(1, n):
        if tr[i] is None:
            prev = None
            continue
        if prev is None:
            if i < period:
                continue
            window = tr[i - period + 1:i + 1]
            if any(x is None for x in window):
                continue
            prev = sum(window) / period
            out[i] = prev
        else:
            prev = (prev * (period - 1) + tr[i]) / period
            out[i] = prev
    return out


def macd(values, fast=12, slow=26, signal=9):
    """MACD(fast,slow,signal) over a price list -> aligned {macd, signal, hist} lists."""
    ef, es = ema(values, fast), ema(values, slow)
    line = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist = [(m - s) if (m is not None and s is not None) else None for m, s in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def ibs(highs, lows, closes):
    """Internal bar strength (close-low)/(high-low) in [0,1]; None on missing data or a
    zero-range bar (high == low), which has no defined position-in-range."""
    out = [None] * len(closes)
    for i in range(len(closes)):
        h, l, c = highs[i], lows[i], closes[i]
        if None in (h, l, c) or h == l:
            continue
        out[i] = (c - l) / (h - l)
    return out


def zscore(values, period):
    """(value - SMA(period)) / population stdev over the same window; None during warmup, on a
    gap inside the window, or when the window is flat (stdev 0 — no defined deviation)."""
    out = [None] * len(values)
    if period <= 1:
        return out
    for i in range(len(values)):
        if i < period - 1:
            continue
        window = values[i - period + 1:i + 1]
        if any(v is None for v in window):
            continue
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        if var <= 0:
            continue
        out[i] = (values[i] - mean) / (var ** 0.5)
    return out
