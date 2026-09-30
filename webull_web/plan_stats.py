"""Pure statistics for the plan pages (spec 2026-09-10 plan-first cockpit §3). No I/O, no repo imports.
Only the stdlib's pure maths/date arithmetic (`math`, `statistics`, `datetime.date`) — nothing here reads
a clock, a file or a network.

Per-trade returns are in PERCENT of entry. Variance uses n-1; SD is its square root; SE = SD/sqrt(n).
The histogram has fixed 0.5% bins from -10 to +10 plus two open tail bins so one outlier never flattens
the chart. The fitted curve is the normal density with the sample mean/SD scaled so its area equals the bar
count; the "expected" curve uses the backtest's mean/SD but the ACTUAL n so the two areas are comparable."""
from __future__ import annotations

import heapq
import math
import statistics
from datetime import date

BIN_LO = -10.0
BIN_HI = 10.0
BIN_W = 0.5
SMALL_SAMPLE_N = 30
TRADING_DAYS = 252          # sessions per year: the annualisation factor for a daily series
CALENDAR_DAYS = 365         # calendar days per year: the annualisation factor for a dated span


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs)


def max_drawdown_pct(values: list[float]) -> float | None:
    """Largest peak-to-trough fall of a growth index, in percent of the peak. [] -> None."""
    if not values:
        return None
    peak = values[0]
    worst = 0.0
    for v in values:
        if v > peak:
            peak = v
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return round(worst, 6)


def _shape(r: list[float], mean: float, sd: float | None) -> tuple[float | None, float | None]:
    """Sample skewness (G1) and excess kurtosis (G2), both bias-corrected — the two things a mean and an
    SD cannot say: which tail is longer, and how fat both tails are. z = (x − mean)/SD on the n−1 SD;
    skew needs n >= 3 and kurtosis n >= 4 (their corrections divide by n−2 and n−3), and neither exists
    without a spread to measure against."""
    n = len(r)
    if sd is None or sd <= 0:
        return None, None
    z = [(x - mean) / sd for x in r]
    skew = n / ((n - 1) * (n - 2)) * sum(v ** 3 for v in z) if n >= 3 else None
    kurt = None
    if n >= 4:
        kurt = (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3)) * sum(v ** 4 for v in z)
                - 3 * (n - 1) ** 2 / ((n - 2) * (n - 3)))
    return (round(skew, 6) if skew is not None else None, round(kurt, 6) if kurt is not None else None)


def _streaks(r: list[float]) -> tuple[int, int, dict]:
    """(longest win run, longest loss run, the run still open) IN THE ORDER GIVEN — callers pass returns
    in exit order, so "current" means the most recent trades. A zero return breaks a run and counts for
    neither side: it is not a win the book can bank on, nor a loss it has to recover."""
    longest_win = longest_loss = 0
    kind, length = None, 0
    for x in r:
        side = "win" if x > 0 else "loss" if x < 0 else None
        if side is None:
            kind, length = None, 0
            continue
        length = length + 1 if side == kind else 1
        kind = side
        if side == "win":
            longest_win = max(longest_win, length)
        else:
            longest_loss = max(longest_loss, length)
    return longest_win, longest_loss, {"kind": kind, "len": length}


def describe(returns_pct: list[float], *, pnl_usd: list[float] | None = None, holds: list[float] | None = None,
             growth_index: list[float] | None = None) -> dict | None:
    """The Stats block for one book. Returns None for an empty sample."""
    r = [float(x) for x in returns_pct]
    n = len(r)
    if n == 0:
        return None
    mean = _mean(r)
    variance = sum((x - mean) ** 2 for x in r) / (n - 1) if n >= 2 else None
    sd = math.sqrt(variance) if variance is not None else None
    se = sd / math.sqrt(n) if sd is not None else None
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x < 0]
    avg_win = _mean(wins) if wins else None
    avg_loss = -_mean(losses) if losses else None            # positive magnitude: E = p·W − (1−p)·L
    band_lo = round(mean - 1.96 * se, 6) if se is not None else None
    band_hi = round(mean + 1.96 * se, 6) if se is not None else None
    resolve = math.ceil((1.96 * sd / mean) ** 2) if (sd is not None and mean > 0) else None
    # Profit factor: dollars (well, percents) won per percent lost. No loss at all is not "infinitely
    # good", it is unmeasured -- None; losses with no win is a real, measured 0.0.
    won, lost = sum(wins), -sum(losses)
    profit_factor = None if not losses else (round(won / lost, 6) if wins else 0.0)
    skew, kurt = _shape(r, mean, sd)
    longest_win, longest_loss, current = _streaks(r)
    return {
        "n": n,
        "mean_pct": round(mean, 6),
        "variance": round(variance, 6) if variance is not None else None,
        "sd_pct": round(sd, 6) if sd is not None else None,
        "se_pct": round(se, 6) if se is not None else None,
        "win_rate": round(sum(1 for x in r if x > 0) / n, 6),
        "expectancy_usd": round(_mean([float(p) for p in pnl_usd]), 2) if pnl_usd else None,
        "best_pct": round(max(r), 4),
        "worst_pct": round(min(r), 4),
        "mean_hold": round(_mean([float(h) for h in holds]), 2) if holds else None,
        "max_drawdown_pct": max_drawdown_pct([float(v) for v in growth_index]) if growth_index else None,
        "return_per_risk": round(mean / sd, 6) if sd else None,
        "avg_win_pct": round(avg_win, 6) if avg_win is not None else None,
        "avg_loss_pct": round(avg_loss, 6) if avg_loss is not None else None,
        "band95_lo_pct": band_lo, "band95_hi_pct": band_hi,
        "trades_to_resolve": resolve,
        "median_pct": round(statistics.median(r), 6),
        "profit_factor": profit_factor,
        "payoff_ratio": round(avg_win / avg_loss, 6) if (avg_win is not None and avg_loss is not None) else None,
        "skewness": skew, "excess_kurtosis": kurt,
        "longest_win": longest_win, "longest_loss": longest_loss, "current_streak": current,
    }


def _empty_bins() -> list[dict]:
    n_bins = int(round((BIN_HI - BIN_LO) / BIN_W))
    bins = [{"lo": None, "hi": BIN_LO, "center": BIN_LO - BIN_W / 2, "count": 0, "trades": []}]
    for i in range(n_bins):
        lo = BIN_LO + i * BIN_W
        bins.append({"lo": lo, "hi": lo + BIN_W, "center": lo + BIN_W / 2, "count": 0, "trades": []})
    bins.append({"lo": BIN_HI, "hi": None, "center": BIN_HI + BIN_W / 2, "count": 0, "trades": []})
    return bins


def histogram(returns_pct: list[float]) -> list[dict]:
    """42 bins: low tail (-inf, -10), 40 x 0.5% bins [lo, hi), high tail [10, inf). Each bin keeps trade indices."""
    bins = _empty_bins()
    n_bins = len(bins) - 2
    for idx, x in enumerate(returns_pct):
        x = float(x)
        if x < BIN_LO:
            b = 0
        elif x >= BIN_HI:
            b = len(bins) - 1
        else:
            b = 1 + min(int((x - BIN_LO) / BIN_W + 1e-9), n_bins - 1)
        bins[b]["count"] += 1
        bins[b]["trades"].append(idx)
    return bins


def normal_curve(centers: list[float], mean: float, sd: float | None, n: int, bin_width: float = BIN_W) -> list[float]:
    """Normal density at each centre, scaled by n * bin_width so the curve's area equals the bar count."""
    if sd is None or sd <= 0 or n <= 0:
        return [0.0] * len(centers)
    k = n * bin_width / (sd * math.sqrt(2 * math.pi))
    return [round(k * math.exp(-0.5 * ((c - mean) / sd) ** 2), 6) for c in centers]


def distribution(returns_pct: list[float], *, expected_mean: float | None = None, expected_sd: float | None = None,
                 expected_n: int | None = None) -> dict:
    """The distribution block: bins + fitted curve + (optional) expected curve on the same axis."""
    r = [float(x) for x in returns_pct]
    n = len(r)
    bins = histogram(r)
    centers = [b["center"] for b in bins]
    mean = _mean(r) if n else None
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / (n - 1)) if n >= 2 else None
    fitted = normal_curve(centers, mean, sd, n) if n else [0.0] * len(centers)
    has_expected = expected_mean is not None and expected_sd is not None and expected_sd > 0
    expected = normal_curve(centers, float(expected_mean), float(expected_sd), n) if has_expected else None
    return {"bins": bins, "fitted": fitted, "expected": expected,
            "mean": round(mean, 4) if mean is not None else None, "sd": round(sd, 4) if sd is not None else None, "n": n,
            "expected_mean": expected_mean if has_expected else None, "expected_sd": expected_sd if has_expected else None,
            "expected_n": expected_n if has_expected else None}


def convergence(returns_pct: list[float]) -> list[dict]:
    """Cumulative mean after each trade with its standard error (n-1 variance; None below n=2)."""
    out, acc = [], []
    for x in returns_pct:
        acc.append(float(x))
        n = len(acc)
        mean = _mean(acc)
        se = None
        if n >= 2:
            var = sum((v - mean) ** 2 for v in acc) / (n - 1)
            se = math.sqrt(var) / math.sqrt(n)
        out.append({"n": n, "cum_mean_pct": round(mean, 6), "se_pct": round(se, 6) if se is not None else None})
    return out


def excursions(entry_price: float, closes: list[float]) -> tuple[float | None, float | None]:
    """(MFE, MAE) in percent of entry over the closes of the hold; (None, None) without closes or a positive entry."""
    if not closes or not entry_price or entry_price <= 0:
        return None, None
    c = [float(x) for x in closes]
    return round((max(c) / entry_price - 1.0) * 100.0, 4), round((min(c) / entry_price - 1.0) * 100.0, 4)


def by_year_from_curve(points: list[dict]) -> list[dict]:
    """Calendar-year returns of an equity/index curve: each year's last value over the previous year's
    last value; the first year over its own first point."""
    pts = [(str(p["date"]), float(p["equity"] if p.get("equity") is not None else p.get("value")))
           for p in points if p.get("date") and (p.get("equity") is not None or p.get("value") is not None)]
    if not pts:
        return []
    pts.sort()
    out, base = [], pts[0][1]
    year, last = pts[0][0][:4], pts[0][1]
    for d, v in pts:
        if d[:4] != year:
            out.append({"year": int(year), "return_pct": round((last / base - 1.0) * 100.0, 4)})
            base, year = last, d[:4]
        last = v
    out.append({"year": int(year), "return_pct": round((last / base - 1.0) * 100.0, 4)})
    return out


HOLD_BUCKETS = tuple(str(i) for i in range(1, 11)) + ("11+",)


def hold_histogram(holds: list[float]) -> list[dict]:
    counts = {b: 0 for b in HOLD_BUCKETS}
    for h in holds:
        d = max(1, int(round(float(h))))
        counts["11+" if d > 10 else str(d)] += 1
    return [{"label": b, "count": counts[b]} for b in HOLD_BUCKETS]


def by_symbol(trades: list[dict]) -> list[dict]:
    groups: dict[str, list[float]] = {}
    for t in trades:
        groups.setdefault(str(t["symbol"]), []).append(float(t["return_pct"]))
    out = []
    for sym in sorted(groups):
        r = groups[sym]
        out.append({"symbol": sym, "n": len(r), "win_rate": round(sum(1 for x in r if x > 0) / len(r), 6),
                    "mean_pct": round(_mean(r), 6), "best_pct": round(max(r), 4), "worst_pct": round(min(r), 4)})
    return out


# ---------------------------------------------------------------- over time (a dated growth index)
# Per-trade statistics say how good each bet was; these say what holding the book through the calendar
# actually returned and cost. Every one of them needs DATES, so they are computed over a growth index
# ([{date, value}] ascending, values > 0) rather than over the trade list.


def _dated(points: list[dict] | None) -> list[tuple[str, float]]:
    """[{date, value}] -> ascending (YYYY-MM-DD, value) pairs. A row without both, or with a value at or
    below zero, is dropped: a log return and a percent-of-peak drawdown are both undefined there."""
    out: list[tuple[str, float]] = []
    for p in points or []:
        d, v = p.get("date"), p.get("value")
        if d is None or v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if fv > 0:
            out.append((str(d)[:10], fv))
    out.sort()
    return out


def _span_days(a: str, b: str) -> int | None:
    try:
        return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except ValueError:
        return None


def _underwater_days(pts: list[tuple[str, float]]) -> int | None:
    """The longest calendar-day span from a peak to the first day the index regains it. A drawdown still
    open at the last point counts to that last date -- the book has been under water that long, and
    calling it zero would flatter exactly the drawdown that matters most."""
    if not pts:
        return None
    peak_v, peak_d = pts[0][1], pts[0][0]
    longest, under = 0, False
    for d, v in pts[1:]:
        if v >= peak_v:
            if under:
                longest = max(longest, _span_days(peak_d, d) or 0)
                under = False
            peak_v, peak_d = v, d
        else:
            under = True
    if under:
        longest = max(longest, _span_days(peak_d, pts[-1][0]) or 0)
    return longest


def time_stats(points: list[dict]) -> dict | None:
    """The over-time block for one growth index: what a year of it returned (CAGR), how much it moved
    getting there (annualised volatility of the daily log returns), the ratio of the two (Sharpe, zero
    risk-free rate -- the plan's own comparison is the backtest, not cash), and the drawdown story (depth,
    length, where it stands now, and CAGR per unit of depth). None for an empty index."""
    pts = _dated(points)
    if not pts:
        return None
    values = [v for _, v in pts]
    span = _span_days(pts[0][0], pts[-1][0])
    cagr = None
    if span is not None and span >= 1:
        cagr = ((values[-1] / values[0]) ** (CALENDAR_DAYS / span) - 1.0) * 100.0
    logs = [math.log(b / a) for a, b in zip(values, values[1:])]
    sd = statistics.stdev(logs) if len(logs) >= 2 else None
    peak = max(values)
    mdd = max_drawdown_pct(values)
    return {
        "sessions": len(pts), "span_days": span,
        "cagr_pct": round(cagr, 6) if cagr is not None else None,
        "ann_vol_pct": round(sd * math.sqrt(TRADING_DAYS) * 100.0, 6) if sd is not None else None,
        "sharpe": round(statistics.mean(logs) / sd * math.sqrt(TRADING_DAYS), 6) if sd else None,
        "max_dd_pct": mdd, "max_dd_days": _underwater_days(pts),
        "current_dd_pct": round((peak - values[-1]) / peak * 100.0, 6),
        "calmar": round(cagr / mdd, 6) if (cagr is not None and mdd) else None,
    }


def underwater(points: list[dict]) -> list[dict]:
    """The drawdown curve: how far below its own running peak the index sat on each date, in percent of
    that peak (>= 0, zero at every new high). The shape a growth curve hides."""
    out, peak = [], None
    for d, v in _dated(points):
        peak = v if peak is None or v > peak else peak
        out.append({"date": d, "dd_pct": round((peak - v) / peak * 100.0, 6)})
    return out


def utilisation(trades: list[dict], equity_points: list[dict]) -> dict | None:
    """How much of the book was actually working. For each equity point (date d, equity e): a trade is
    open when entry_date <= d < exit_date (the exit is at that day's open, so the day of the exit is out),
    and the capital at work is the sum of shares x entry_price over those trades as a percent of e.
    A trade with no `shares` still counts as a lot but adds no capital -- the lot count stays honest and
    the percent never guesses at a size. The ratio needs DOLLAR equity: a growth index would divide
    dollars by 100-ish and read as thousands of percent. None without points.

    Swept with a heap rather than re-scanning the trades per point: the backtest is ~3k trades over ~5k
    sessions, and the double loop would be 16M comparisons on every page build."""
    pts = _dated(equity_points)
    if not pts:
        return None
    rows: list[tuple[str, str, float]] = []
    for t in trades or []:
        entry, exit_ = str(t.get("entry_date") or "")[:10], str(t.get("exit_date") or "")[:10]
        if not entry or not exit_:
            continue
        shares, price = t.get("shares"), t.get("entry_price")
        try:
            cap = float(shares) * float(price) if shares is not None and price is not None else 0.0
        except (TypeError, ValueError):
            cap = 0.0
        rows.append((entry, exit_, cap))
    rows.sort()
    active: list[tuple[str, float]] = []            # heap keyed by exit date
    i, at_work, lots, pct_sum, pct_n = 0, 0.0, 0, 0.0, 0
    for d, e in pts:
        while i < len(rows) and rows[i][0] <= d:
            heapq.heappush(active, (rows[i][1], rows[i][2]))
            at_work += rows[i][2]
            i += 1
        while active and active[0][0] <= d:
            at_work -= heapq.heappop(active)[1]
        lots += len(active)
        if e > 0:
            pct_sum += at_work / e * 100.0
            pct_n += 1
    return {"avg_open_lots": round(lots / len(pts), 6),
            "avg_capital_at_work_pct": round(pct_sum / pct_n, 6) if pct_n else None,
            "sessions": len(pts)}
