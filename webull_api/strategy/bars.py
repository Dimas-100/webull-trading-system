"""Normalize webull_api.market_data.get_bars output into the chronological OHLCV list the
backtest engine expects. Bars arrive newest-first with string values; defensive about field names."""
from __future__ import annotations

# Strategy timeframe (schema enum) -> SDK Timespan name. One source of truth for the strategy MCP
# and the Lab.
TIMEFRAME_TO_TIMESPAN = {"1m": "M1", "5m": "M5", "1H": "M60", "1D": "D", "1W": "W"}

from webull_api._util import num as _num  # canonical defensive numeric extractor


def _str(r, keys):
    for k in keys:
        v = r.get(k)
        if v is not None and v != "":
            return str(v)
    return ""


def to_ohlcv(raw):
    if isinstance(raw, list):
        rows = raw
    elif isinstance(raw, dict) and isinstance(raw.get("data"), list):
        rows = raw["data"]
    else:
        rows = []
    bars = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        bars.append({
            "time": _str(r, ["time", "t", "timestamp"]),
            "open": _num(r, ["open", "o"]),
            "high": _num(r, ["high", "h"]),
            "low": _num(r, ["low", "l"]),
            "close": _num(r, ["close", "c"]),
            "volume": _num(r, ["volume", "v"]),
        })
    # Newest-first -> oldest-first, ORDER-AWARE. The old unconditional reverse() assumed raw
    # newest-first input; feeding it already-normalized (oldest-first) bars silently produced a
    # TIME-REVERSED series. The lab did exactly that (its injected get_bars normalizes, then
    # orchestrator.fetch_basket_bars/first_available_bars re-applied to_ohlcv), so 31 cycles of
    # Gate-A screening and every cycle regime ran on mirrored data — a 3-year SPY uptrend
    # classified "down/low" 31/31 (found 2026-08-15). Reverse only when the series is NOT
    # provably ascending: raw newest-first still reverses, an oldest-first input is preserved,
    # and unknowable order (empty/equal timestamps) keeps the historical behavior.
    if not (len(bars) >= 2 and str(bars[0]["time"]) < str(bars[-1]["time"])):
        bars.reverse()
    return [b for b in bars if None not in (b["open"], b["high"], b["low"], b["close"])]
