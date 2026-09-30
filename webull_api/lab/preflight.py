"""Cycle-start bar-sanity preflight. Pure, no I/O — the lab_service shell feeds it the fetched
basket and REFUSES to run the cycle (no screening, no M consumed) when it reports problems.

Generalizes the 2026-08-15 reversed-bars lesson: `to_ohlcv` is now idempotent at its own seam,
but the bug CLASS — a silently malformed feed screened for 31 cycles — deserves a guard on the
whole feed, not one seam. Checks are deliberately cheap and shape-only (ordering, recency, OHLC
coherence); anything statistical belongs to Gate A."""
from __future__ import annotations

from datetime import date, timedelta

# Last bar must be within this many calendar days of today: Monday after a holiday weekend is
# 4 days from the prior Thursday close, so 5 covers every normal market gap.
MAX_STALE_CALENDAR_DAYS = 5


def _bar_date(bar: dict) -> date | None:
    try:
        return date.fromisoformat(str(bar.get("time", ""))[:10])
    except ValueError:
        return None


def check_bars(bars_by_symbol: dict[str, list[dict]], *, today_et: str) -> list[str]:
    """Human-readable problems with the fetched basket bars; [] means clean. Every symbol is
    checked (one bad symbol never masks another): non-empty, parseable STRICTLY-ASCENDING
    dates, last bar recent, and per-bar OHLC coherence (low <= open/close <= high, prices > 0)."""
    try:
        today = date.fromisoformat(today_et)
    except ValueError:
        return [f"today_et unparseable: {today_et!r}"]
    problems: list[str] = []
    for sym in sorted(bars_by_symbol):
        bars = bars_by_symbol[sym]
        if not bars:
            problems.append(f"{sym}: empty bar series")
            continue
        dates = [_bar_date(b) for b in bars]
        if any(d is None for d in dates):
            problems.append(f"{sym}: unparseable bar timestamp")
            continue
        if any(b >= a for a, b in zip(dates[1:], dates)):
            problems.append(f"{sym}: bar dates not strictly ascending")
            continue
        if today - dates[-1] > timedelta(days=MAX_STALE_CALENDAR_DAYS):
            problems.append(f"{sym}: stale — last bar {dates[-1].isoformat()}")
        for b in bars:
            try:
                o, h, low, c = (float(b["open"]), float(b["high"]),
                                float(b["low"]), float(b["close"]))
            except (KeyError, TypeError, ValueError):
                problems.append(f"{sym}: malformed OHLC fields")
                break
            if min(o, h, low, c) <= 0 or not (low <= o <= h and low <= c <= h):
                problems.append(f"{sym}: incoherent OHLC at {b.get('time')}")
                break
    return problems


def short_history(bars_by_symbol: dict[str, list[dict]], *, min_bars: int) -> list[tuple[str, int]]:
    """Symbols (sorted) whose fetched bar count is below `min_bars` (spec 2026-09-07 tiingo depth
    §7): Gate A's walk-forward split needs the full lookback per symbol, and it fails the WHOLE
    candidate on one short basket symbol rather than reporting why — the shell refuses the cycle
    first instead. Pure; [] means every symbol has enough history (or the dict is empty)."""
    return sorted((sym, len(bars)) for sym, bars in bars_by_symbol.items() if len(bars) < min_bars)


def fresh_bar_day(bars_by_symbol: dict[str, list[dict]], *, today_et: str) -> bool:
    """True when at least one basket symbol's newest bar is dated `today_et`. On a weekend or an
    NYSE holiday the feed is clean and recent (check_bars passes) but there is nothing new to
    screen against — a cycle that generates candidates anyway spends permanent trial budget (M)
    for no information (Labor Day 2026-09-07 cost 64 trials). The shell refuses to generate on
    such a day. Pure; unparseable timestamps count as not-fresh."""
    for bars in bars_by_symbol.values():
        if bars and _bar_date(bars[-1]) is not None and _bar_date(bars[-1]).isoformat() == today_et:
            return True
    return False
