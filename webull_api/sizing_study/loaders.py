"""Inputs for the sizing and stacking study (spec docs/superpowers/specs/2026-09-10-sizing-stacking-study-design.md §2).

Two already-produced trade lists become ONE dated signal stream, and the daily adjusted closes that mark
open lots become a calendar-aligned lookup. Pure apart from the two readers injected at the edges
(``store.read`` for prices, ``json`` for the trade lists), so the tests run on synthetic input.

The trade lists are the study's *inputs*, not a signal generator: each row is one trade its own book
actually took under its own slot/cash rules. This study re-sizes those trades; it cannot recover a
signal the source book skipped. Declared in §2 ("inputs are two trade lists that already exist").
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from webull_api.paths import REPO_ROOT

RSI2_TRADES = Path("docs") / "reviews" / "2026-09-07-rsi2-backtest-tiingo.json"
IBS_TRADES = Path("docs") / "reviews" / "2026-09-10-ibs-book-round5-develop-trades.json"

RSI2 = "rsi2"
IBS = "ibs"
BOOK_ORDER = {RSI2: 0, IBS: 1}          # §3: "RSI2 before IBS, then by symbol"
CALENDAR_SYMBOL = "SPY"                 # §3: the SPY calendar


@dataclass(frozen=True)
class Signal:
    """One trade from a source book, normalised: enter on ``entry_date``, settle on ``exit_date``
    at ``return_pct`` (percent, already net of the source book's 1c/side)."""
    book: str
    symbol: str
    entry_date: str
    exit_date: str
    return_pct: float
    side: str = "long"        # "long" | "short" -- decides how an OPEN lot is marked (book.run step 3); P&L is return_pct either way

    @property
    def order_key(self) -> tuple:
        return (self.entry_date, BOOK_ORDER.get(self.book, 9), self.symbol)


def _rows(path: Path) -> list[dict]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return list(payload.get("trades") or [])


def signals_from(rsi2_rows: list[dict], ibs_rows: list[dict]) -> list[Signal]:
    """Both lists carry symbol/entry_date/exit_date/return_pct; only the price keys differ
    (RSI2 entry_price/exit_price, IBS entry/exit), and prices are not used — return_pct settles a lot."""
    out: list[Signal] = []
    for book, rows in ((RSI2, rsi2_rows), (IBS, ibs_rows)):
        for r in rows:
            entry, exit_, sym = r.get("entry_date"), r.get("exit_date"), r.get("symbol")
            if not entry or not exit_ or not sym or r.get("return_pct") is None:
                continue
            out.append(Signal(book, str(sym), str(entry)[:10], str(exit_)[:10], float(r["return_pct"])))
    out.sort(key=lambda s: s.order_key)
    return out


def load_signals(repo_root: Path = REPO_ROOT) -> list[Signal]:
    return signals_from(_rows(repo_root / RSI2_TRADES), _rows(repo_root / IBS_TRADES))


def by_entry_date(signals: list[Signal]) -> dict[str, list[Signal]]:
    """Entry-ordered signals grouped by entry date (each day already in §3's fixed order)."""
    out: dict[str, list[Signal]] = {}
    for s in sorted(signals, key=lambda s: s.order_key):
        out.setdefault(s.entry_date, []).append(s)
    return out


class Marks:
    """Adjusted closes aligned to the study calendar, carried forward.

    ``value[sym][i]`` is the symbol's last adj_close on or before ``calendar[i]`` (None before its
    first row), so a lot's mark on session i is ``size * value[sym][i] / value[sym][entry_i]`` — §2's
    "entry size x (adj_close today / adj_close on the entry session)" with a symbol holiday or a
    missing row carried forward rather than dropped."""

    def __init__(self, calendar: list[str], closes_by_symbol: dict[str, list[dict]]):
        self.calendar = list(calendar)
        self.index = {d: i for i, d in enumerate(self.calendar)}
        self.value: dict[str, list[float | None]] = {}
        for sym, rows in closes_by_symbol.items():
            self.value[sym] = self._align(rows)

    def _align(self, rows: list[dict]) -> list[float | None]:
        ordered = sorted((r for r in rows if r.get("date") and r.get("adj_close") is not None),
                         key=lambda r: r["date"])
        out: list[float | None] = []
        j, last = 0, None
        for d in self.calendar:
            while j < len(ordered) and ordered[j]["date"] <= d:
                last = float(ordered[j]["adj_close"])
                j += 1
            out.append(last)
        return out

    def ratio(self, symbol: str, entry_i: int, i: int) -> float | None:
        """adj_close(i) / adj_close(entry_i), or None when either side is unknown."""
        series = self.value.get(symbol)
        if series is None or not (0 <= entry_i < len(series)) or not (0 <= i < len(series)):
            return None
        now, base = series[i], series[entry_i]
        if now is None or base is None or base <= 0:
            return None
        return now / base


def calendar_from(rows: list[dict], start: str, end: str) -> list[str]:
    return [r["date"] for r in sorted(rows, key=lambda r: r["date"]) if start <= r["date"] <= end]


def load_marks(symbols, calendar: list[str], read) -> Marks:
    return Marks(calendar, {sym: read(sym) for sym in sorted(set(symbols))})
