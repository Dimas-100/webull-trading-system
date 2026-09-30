"""Tiingo store rows -> the toolkit's normalized OHLCV bars, with the get_bars(symbol, timespan, count)
shape the Lab and the backtests inject (spec 2026-09-07 tiingo depth §3.3). Oldest-first, time as
YYYY-MM-DD, adjusted columns by default; idempotent through to_ohlcv (the 2026-08-15 seam lesson)."""
from __future__ import annotations

from datetime import date

from . import store

_ADJ = {"open": "adj_open", "high": "adj_high", "low": "adj_low", "close": "adj_close", "volume": "adj_volume"}
_RAW = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume"}


def to_bars(rows: list[dict], *, adjusted: bool = True) -> list[dict]:
    cols = _ADJ if adjusted else _RAW
    out = [{"time": r["date"], **{k: float(r[c]) for k, c in cols.items()}}
           for r in rows if r.get("date")]
    out.sort(key=lambda b: b["time"])
    return out


def _iso_week(d: str) -> tuple[int, int]:
    y, w, _ = date.fromisoformat(d).isocalendar()
    return y, w


def weekly(bars: list[dict]) -> list[dict]:
    """Daily -> weekly, Mon–Fri, labelled by the week's last session."""
    out: list[dict] = []
    cur_key = None
    cur: dict | None = None
    for b in bars:
        key = _iso_week(b["time"])
        if key != cur_key:
            if cur:
                out.append(cur)
            cur_key, cur = key, dict(b)
        else:
            cur["time"] = b["time"]
            cur["high"] = max(cur["high"], b["high"])
            cur["low"] = min(cur["low"], b["low"])
            cur["close"] = b["close"]
            cur["volume"] += b["volume"]
    if cur:
        out.append(cur)
    return out


def make_get_bars(read=store.read):
    def get_bars(symbol: str, timespan: str = "D", count: str | int = "1200", **_ignored) -> list[dict]:
        rows = read(symbol)
        if not rows:
            raise KeyError(symbol)
        n = max(1, int(count))
        bars = to_bars(rows)
        if timespan == "D":
            return bars[-n:]
        if timespan == "W":
            return weekly(bars)[-n:]
        raise ValueError(f"tiingo bars: unsupported timespan {timespan!r} (D or W)")
    return get_bars
