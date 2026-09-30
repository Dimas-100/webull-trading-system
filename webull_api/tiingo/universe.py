"""Point-in-time day-trade universe from the broad daily store (spec 2026-09-08 orb backtest §3.3).
Pure: the store reader and the session calendar are injected. Takes the band as plain values —
this module never imports the day-session package (guard)."""
from __future__ import annotations

import csv
import functools
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from webull_api.tiingo import store

ADV_WINDOW = 20


@dataclass(frozen=True)
class Band:
    name: str            # profile name, e.g. "plan-v1"; names the cache directory
    price_min: float
    price_max: float
    min_adv: float       # shares/day, inclusive


def in_band_days(daily_rows, calendar: list[str], band: Band) -> list[str]:
    """Calendar days (ISO, ascending) on which the symbol is in band using only rows dated BEFORE
    each day: prior close inside [price_min, price_max] and the mean raw volume of the last
    ADV_WINDOW prior rows >= min_adv. Fewer than ADV_WINDOW prior rows -> never in band."""
    rows = sorted((r for r in daily_rows or [] if r.get("date")), key=lambda r: r["date"])
    out: list[str] = []
    vols: list[float] = []
    j = 0
    for day in calendar:
        while j < len(rows) and rows[j]["date"] < day:
            vols.append(float(rows[j].get("volume") or 0.0))
            j += 1
        if j < ADV_WINDOW:
            continue
        close = rows[j - 1].get("close")
        if close is None or not (band.price_min <= float(close) <= band.price_max):
            continue
        if sum(vols[-ADV_WINDOW:]) / ADV_WINDOW >= band.min_adv:
            out.append(day)
    return out


def in_band(daily_rows, day: date, band: Band) -> bool:
    return in_band_days(daily_rows, [day.isoformat()], band) == [day.isoformat()]


def latest_in_band(daily_rows, band: Band, *, not_before: str | None = None) -> bool:
    """Point-in-time membership off the LAST stored row only (no calendar): raw close in
    [price_min, price_max] and the mean raw volume of the trailing ADV_WINDOW rows >= min_adv.
    Fewer than ADV_WINDOW rows -> False. `not_before` (ISO date) rejects a stale last row -- the
    nightly auto-universe build must never quietly serve a symbol whose feed went dark
    (spec 2026-09-08 auto universe §1)."""
    rows = sorted((r for r in daily_rows or [] if r.get("date")), key=lambda r: r["date"])
    if len(rows) < ADV_WINDOW:
        return False
    last = rows[-1]
    if not_before is not None and last["date"] < not_before:
        return False
    close = last.get("close")
    if close is None or not (band.price_min <= float(close) <= band.price_max):
        return False
    window = rows[-ADV_WINDOW:]
    adv = sum(float(r.get("volume") or 0.0) for r in window) / ADV_WINDOW
    return adv >= band.min_adv


def pool(symbols, band: Band, *, read=store.read, not_before: str | None = None) -> list[str]:
    """Sorted names whose store rows pass `latest_in_band` -- the point-in-time day-trade
    universe (spec 2026-09-08 auto universe §1)."""
    return sorted(s for s in symbols if latest_in_band(read(s), band, not_before=not_before))


def calendar(spy_rows, start: str, end: str) -> list[str]:
    return sorted(r["date"] for r in spy_rows or [] if start <= r["date"] <= end)


def cache_dir(name: str) -> Path:
    return store.dir_path() / "universe" / name


def write_cache(name: str, cal: list[str], by_day: dict[str, list[str]]) -> None:
    """One `<year>.csv` per year of `cal` under `cache_dir(name)`, header `date,symbols`, symbols
    space-separated and sorted; each year written to a `.tmp` and `os.replace`d, so a reader never
    sees a half-written year. Clears the year memo -- the files just changed under it.
    Public because a builder that must VALIDATE its result before overwriting a cache
    (`scripts/build_rank_universe.py`) has to keep the compute and the write apart."""
    d = cache_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    years = sorted({day[:4] for day in cal})
    for y in years:
        tmp = d / f"{y}.csv.tmp"
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "symbols"])
            for day in cal:
                if day[:4] == y:
                    w.writerow([day, " ".join(sorted(by_day.get(day) or []))])
        os.replace(tmp, d / f"{y}.csv")
    _year_rows.cache_clear()


def build_cache(start: str, end: str, symbols, band: Band, *, read=store.read, calendar: list[str]) -> dict[str, list[str]]:
    cal = [d for d in calendar if start <= d <= end]
    by_day: dict[str, list[str]] = {d: [] for d in cal}
    for sym in symbols:
        for d in in_band_days(read(sym), cal, band):
            by_day[d].append(sym)
    write_cache(band.name, cal, by_day)
    return {k: sorted(v) for k, v in by_day.items()}


# ── rank-based universes (spec 2026-09-21 swing universe round §2) ────────────────────────────
MAX_STALE_SESSIONS = 5


@dataclass(frozen=True)
class RankSpec:
    """A point-in-time universe defined by a RANK, not a threshold: the `top_n` names by dollar
    ADV whose prior RAW close sits inside the band. `name` names the cache directory.
    `max_stale_sessions` is how far back the newest printed row may be and still count."""
    name: str
    top_n: int
    price_min: float
    price_max: float
    adv_window: int = ADV_WINDOW
    max_stale_sessions: int = MAX_STALE_SESSIONS

    def __post_init__(self):
        if self.top_n < 1:
            raise ValueError(f"top_n must be >= 1: {self.top_n!r}")


def _close_of(row: dict) -> float | None:
    """The row's RAW close -- the price that actually printed that day, and the only one that is
    point-in-time: the store's `adj_close` is restated by every LATER split or dividend, so both
    the rank and the band would otherwise read post-D information into day D (spec amended
    2026-09-21). `adj_close` is a fallback for a row carrying no raw close at all; every row the
    store writes carries both."""
    for key in ("close", "adj_close"):
        v = row.get(key)
        if v is not None:
            return float(v)
    return None


def _prior_stats(daily_rows, days: list[str], window: int, max_stale: int | None = None):
    """Yield `(day, prior_close, dollar_adv)` for each day in `days` (ascending ISO), computed
    from rows dated STRICTLY BEFORE that day: the last prior raw close, and the mean of
    `close x volume` over the last `window` prior rows. `prior_close` is None with no prior row,
    `dollar_adv` is None with fewer than `window` of them. Nothing dated on or after the day is
    read -- that is the whole point-in-time guarantee.

    With `max_stale` set, a day whose newest prior row is more than that many CALENDAR sessions
    back yields `(day, None, None)`: a feed that went dark must not hold a universe slot on a
    frozen average. The bound only applies once the day has that many sessions behind it inside
    `days`, so a healthy name is not dropped at the very start of the window."""
    rows = sorted((r for r in daily_rows or [] if r.get("date")), key=lambda r: r["date"])
    dollars: list[float] = []
    j = 0
    for i, day in enumerate(days):
        while j < len(rows) and rows[j]["date"] < day:
            close = _close_of(rows[j])
            dollars.append((close or 0.0) * float(rows[j].get("volume") or 0.0))
            j += 1
        if j == 0:                                        # nothing has printed before this day
            yield day, None, None
            continue
        if max_stale is not None and i >= max_stale and rows[j - 1]["date"] < days[i - max_stale]:
            yield day, None, None                         # the feed went dark: no usable print
            continue
        adv = sum(dollars[-window:]) / window if (window > 0 and j >= window) else None
        yield day, _close_of(rows[j - 1]), adv


def dollar_adv_series(daily_rows, window: int = ADV_WINDOW) -> dict[str, float]:
    """`{date: mean dollar volume}` for each date in the rows, over the `window` sessions strictly
    BEFORE that date (raw `close x volume`, falling back to `adj_close` only for a row with no raw
    close). A date with fewer than `window` prior sessions is ABSENT from the mapping
    (`.get(d)` is None)."""
    rows = sorted((r for r in daily_rows or [] if r.get("date")), key=lambda r: r["date"])
    days = [r["date"] for r in rows]
    return {d: adv for d, _, adv in _prior_stats(rows, days, window) if adv is not None}


def rank_membership(read, symbols, calendar: list[str], spec: RankSpec) -> dict[str, list[str]]:
    """`{day: sorted symbols}` -- per calendar day, the `spec.top_n` names with the largest dollar
    ADV among those whose PRIOR RAW close is inside `[price_min, price_max]`, whose ADV is defined,
    and which printed a row within `spec.max_stale_sessions` sessions before the day.
    Ranked by ADV descending, ties by symbol (the alphabetically first name wins the slot).

    Memory-conscious by construction: symbols stream one at a time and each day's bucket is sorted
    and trimmed back to `top_n` whenever it grows past twice that -- a dropped name is already
    behind `top_n` better ones on that day, and nothing that follows can raise it."""
    cal = sorted(calendar)
    buckets: dict[str, list[tuple[float, str]]] = {d: [] for d in cal}
    limit = 2 * spec.top_n
    for sym in symbols:
        for day, close, adv in _prior_stats(read(sym), cal, spec.adv_window, spec.max_stale_sessions):
            if adv is None or close is None or not (spec.price_min <= close <= spec.price_max):
                continue
            bucket = buckets[day]
            bucket.append((adv, sym))
            if len(bucket) > limit:
                bucket.sort(key=lambda p: (-p[0], p[1]))
                del bucket[spec.top_n:]
    out: dict[str, list[str]] = {}
    for day, bucket in buckets.items():
        bucket.sort(key=lambda p: (-p[0], p[1]))
        out[day] = sorted(s for _, s in bucket[:spec.top_n])
    return out


def build_rank_cache(start: str, end: str, symbols, spec: RankSpec, *, read=store.read,
                     calendar: list[str]) -> dict[str, list[str]]:
    """`rank_membership` over the clipped calendar, written in `build_cache`'s exact on-disk shape
    (`data/tiingo/universe/<spec.name>/<year>.csv`), so `load`/`load_month` read it unchanged."""
    cal = [d for d in calendar if start <= d <= end]
    by_day = rank_membership(read, symbols, cal, spec)
    write_cache(spec.name, cal, by_day)
    return by_day


@functools.lru_cache(maxsize=64)
def _year_rows(year: str, name: str, _dir: str = "") -> dict[str, list[str]]:
    """One year file, parsed once. The backtest asks for `load(day, profile)` on every session of
    a five-year window, which re-parsed the same ~250-row file 1,250 times per profile.
    `_dir` is unused in the body but PART OF THE KEY: the cache directory follows TIINGO_DIR, so a
    process (or a test) that repoints it must never be served another store's rows.
    `build_cache` clears the cache after writing."""
    p = cache_dir(name) / f"{year}.csv"
    if not p.exists():
        return {}
    with p.open(encoding="utf-8", newline="") as fh:
        return {r["date"]: [s for s in (r["symbols"] or "").split(" ") if s] for r in csv.DictReader(fh)}


def _rows_for(year: str, name: str) -> dict[str, list[str]]:
    return _year_rows(year, name, str(cache_dir(name)))


def load(day: date, name: str) -> list[str]:
    return _rows_for(day.isoformat()[:4], name).get(day.isoformat(), [])


def load_month(ym: str, name: str) -> set[str]:
    out: set[str] = set()
    for d, syms in _rows_for(ym[:4], name).items():
        if d[:7] == ym:
            out.update(syms)
    return out
