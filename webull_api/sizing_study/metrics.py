"""The §5 metrics, computed from a run's marked equity series.

CAGR (compounded, from the marked series); marked max drawdown; Calmar = CAGR / marked max DD (the
selection statistic); worst calendar year; % of months positive; mean utilisation (sum of open-lot
SIZES / equity, averaged over sessions -- sizes, as §5 words it, not marks); trades taken and trades
skipped for cash/slots; and for a stacked cell the monthly correlation of the two books' contributions.
"""
from __future__ import annotations

from datetime import date

from webull_api.sizing_study.book import RunResult

DAYS_PER_YEAR = 365.25


def _d(s: str) -> date:
    return date(int(s[:4]), int(s[5:7]), int(s[8:10]))


def years_between(first: str, last: str) -> float:
    return (_d(last) - _d(first)).days / DAYS_PER_YEAR


def cagr_pct(dates: list[str], equity: list[float], start_equity: float) -> float | None:
    """Compounded from the opening equity to the final mark over the span of the sessions run."""
    if len(dates) < 2 or not equity or start_equity <= 0 or equity[-1] <= 0:
        return None
    yrs = years_between(dates[0], dates[-1])
    if yrs <= 0:
        return None
    return 100.0 * ((equity[-1] / start_equity) ** (1.0 / yrs) - 1.0)


def max_drawdown(dates: list[str], equity: list[float], start_equity: float) -> dict:
    """Deepest peak-to-trough fall of the MARKED series. The opening equity is the first peak, so a
    book that never gets back above its opening still shows its drawdown (peak_date null = the opening)."""
    peak, peak_date = float(start_equity), None
    worst, worst_peak, worst_peak_date, worst_date, worst_trough = 0.0, float(start_equity), None, None, None
    for d, e in zip(dates, equity):
        if e > peak:
            peak, peak_date = e, d
        dd = 0.0 if peak <= 0 else 100.0 * (peak - e) / peak
        if dd > worst:
            worst, worst_peak, worst_peak_date, worst_date, worst_trough = dd, peak, peak_date, d, e
    return {"pct": worst, "peak_date": worst_peak_date, "peak_equity": worst_peak,
            "trough_date": worst_date, "trough_equity": worst_trough}


def calmar(cagr: float | None, mdd_pct: float | None) -> float | None:
    if cagr is None or not mdd_pct or mdd_pct <= 0:
        return None
    return cagr / mdd_pct


def _period_returns(dates: list[str], equity: list[float], start_equity: float,
                    key) -> list[tuple[str, float]]:
    """Return per period (year or month) from the last mark of the previous period; the first
    period runs from the opening equity."""
    ends: list[tuple[str, float]] = []
    for d, e in zip(dates, equity):
        k = key(d)
        if ends and ends[-1][0] == k:
            ends[-1] = (k, e)
        else:
            ends.append((k, e))
    out, prev = [], float(start_equity)
    for k, e in ends:
        out.append((k, 100.0 * (e / prev - 1.0) if prev > 0 else 0.0))
        prev = e
    return out


def yearly_returns(dates, equity, start_equity) -> list[tuple[str, float]]:
    return _period_returns(dates, equity, start_equity, lambda d: d[:4])


def monthly_returns(dates, equity, start_equity) -> list[tuple[str, float]]:
    return _period_returns(dates, equity, start_equity, lambda d: d[:7])


def monthly_points(dates: list[str], equity: list[float]) -> list[dict]:
    """The last session of each month -- the compact stand-in for the daily series in the report."""
    out: list[dict] = []
    for d, e in zip(dates, equity):
        if out and out[-1]["date"][:7] == d[:7]:
            out[-1] = {"date": d, "equity": round(e, 2)}
        else:
            out.append({"date": d, "equity": round(e, 2)})
    return out


def mean_utilisation_pct(invested: list[float], equity: list[float]) -> float | None:
    vals = [100.0 * s / e for s, e in zip(invested, equity) if e > 0]
    return sum(vals) / len(vals) if vals else None


def pearson(a: list[float], b: list[float]) -> float | None:
    """Plain Pearson correlation of two aligned series. lab.dedup.equity_correlation is not the
    helper for this: it takes equity LEVELS and correlates their percentage changes, while §5 asks
    for the correlation of the two books' monthly contributions, which are already returns."""
    n = min(len(a), len(b))
    if n < 2:
        return None
    a, b = a[:n], b[:n]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / ((va ** 0.5) * (vb ** 0.5))


def monthly_book_returns(res: RunResult) -> dict[str, list[tuple[str, float]]]:
    """Each book's monthly contribution, as a share of the equity it started the month with."""
    months: list[str] = []
    for d in res.dates:
        if not months or months[-1] != d[:7]:
            months.append(d[:7])
    base: dict[str, float] = {}
    prev_equity = float(res.start_equity)
    for d, e in zip(res.dates, res.equity):
        if d[:7] not in base:
            base[d[:7]] = prev_equity
        prev_equity = e
    out: dict[str, list[tuple[str, float]]] = {}
    for book, series in res.book_pnl.items():
        by_month: dict[str, float] = {}
        for d, pnl in zip(res.dates, series):
            by_month[d[:7]] = by_month.get(d[:7], 0.0) + pnl
        out[book] = [(m, 100.0 * by_month.get(m, 0.0) / base[m] if base.get(m) else 0.0) for m in months]
    return out


def book_correlation(res: RunResult) -> float | None:
    """Monthly correlation of the two books' contributions (stacked cells only)."""
    series = monthly_book_returns(res)
    if len(series) != 2:
        return None
    a, b = list(series.values())
    return pearson([v for _, v in a], [v for _, v in b])


def _r(v, nd=4):
    return None if v is None else round(v, nd)


def summarize(res: RunResult) -> dict:
    dates, eq = res.dates, res.equity
    cg = cagr_pct(dates, eq, res.start_equity)
    dd = max_drawdown(dates, eq, res.start_equity)
    yearly = yearly_returns(dates, eq, res.start_equity)
    monthly = monthly_returns(dates, eq, res.start_equity)
    worst = min(yearly, key=lambda kv: kv[1]) if yearly else (None, None)
    pos = [v for _, v in monthly if v > 0]
    return {
        "cell": res.cell,
        "sessions": len(dates),
        "start": dates[0] if dates else None,
        "end": dates[-1] if dates else None,
        "start_equity": round(res.start_equity, 2),
        "end_equity": round(res.end_equity, 2),
        "total_return_pct": _r(100.0 * (res.end_equity / res.start_equity - 1.0), 2),
        "cagr_pct": _r(cg, 3),
        "max_drawdown_pct": _r(dd["pct"], 3),
        "drawdown": {"peak_date": dd["peak_date"], "peak_equity": _r(dd["peak_equity"], 2),
                     "trough_date": dd["trough_date"], "trough_equity": _r(dd["trough_equity"], 2)},
        "calmar": _r(calmar(cg, dd["pct"]), 4),
        "worst_year": worst[0],
        "worst_year_pct": _r(worst[1], 3),
        "yearly_returns_pct": {y: _r(v, 3) for y, v in yearly},
        "months": len(monthly),
        "months_positive": len(pos),
        "months_positive_pct": _r(100.0 * len(pos) / len(monthly), 2) if monthly else None,
        "mean_utilisation_pct": _r(mean_utilisation_pct(res.invested, eq), 3),
        "trades_taken": res.taken,
        "trades_taken_by_book": dict(res.taken_by_book),
        "trades_skipped": dict(res.skipped),
        "trades_skipped_total": res.skipped_total,
        "max_open": res.max_open,
        "open_at_end": res.open_at_end,
        "book_monthly_correlation": _r(book_correlation(res), 4),
        "data_notes": {"missing_marks": res.missing_marks,
                       "missing_mark_symbols": dict(sorted(res.missing_mark_symbols.items())),
                       "settled_off_calendar": res.settled_off_calendar,
                       "off_calendar_signals": res.off_calendar_signals},
        "monthly_equity": monthly_points(dates, eq),
    }
