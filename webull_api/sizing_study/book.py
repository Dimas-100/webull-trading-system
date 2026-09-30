"""The §3 book simulator -- one rule set for every cell. Pure: it takes a cell, a calendar, the dated
signal stream and the marks, and returns the marked series. No I/O, no clock, no globals.

Each session, in this order:
  1. settle every lot whose exit_date is today -- cash += size x (1 + return_pct/100);
  2. enter today's signals in the fixed order (RSI2 before IBS, then by symbol) at
     size = E_start_of_day x f, refusing when cash is short or the book is full;
  2b. settle any lot entered today that exits today (same-day trades).
  3. mark -- equity = cash + the open lots' marks -- and record it.

E_start_of_day is the equity carried in from the previous session's mark (the start of the day precedes
the settle), START_EQUITY on the first session. Fractional shares: a lot is a dollar size, never a share
count (§3).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from webull_api.sizing_study.cells import START_EQUITY, Cell
from webull_api.sizing_study.loaders import Marks, Signal


@dataclass(frozen=True)
class Lot:
    book: str
    symbol: str
    entry_date: str
    entry_i: int
    size: float
    exit_date: str
    return_pct: float
    side: str = "long"

    def proceeds(self) -> float:
        return self.size * (1.0 + self.return_pct / 100.0)


@dataclass
class RunResult:
    cell: str
    dates: list[str] = field(default_factory=list)
    equity: list[float] = field(default_factory=list)
    cash: list[float] = field(default_factory=list)
    invested: list[float] = field(default_factory=list)      # sum of OPEN LOT SIZES (§5 utilisation)
    open_count: list[int] = field(default_factory=list)
    book_pnl: dict[str, list[float]] = field(default_factory=dict)   # per session, per book, dollars
    taken: int = 0
    entries: list[Lot] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=lambda: {"slots": 0, "cash": 0})
    taken_by_book: dict[str, int] = field(default_factory=dict)
    missing_marks: int = 0            # lot-sessions marked at cost because a price was unknown
    missing_mark_symbols: dict[str, int] = field(default_factory=dict)
    settled_off_calendar: int = 0     # lots whose exit_date was not a session (settled on the next one)
    off_calendar_signals: int = 0     # signals whose entry_date was not a session in the window
    max_open: int = 0
    open_at_end: int = 0
    start_equity: float = START_EQUITY

    @property
    def end_equity(self) -> float:
        return self.equity[-1] if self.equity else self.start_equity

    @property
    def skipped_total(self) -> int:
        return sum(self.skipped.values())


def run(cell: Cell, calendar: list[str], signals_by_date: dict[str, list[Signal]], marks: Marks,
        start_equity: float = START_EQUITY) -> RunResult:
    res = RunResult(cell=cell.id, start_equity=start_equity)
    res.book_pnl = {b: [] for b in cell.books}
    res.taken_by_book = {b: 0 for b in cell.books}
    sessions = set(calendar)
    for d, sigs in signals_by_date.items():
        if d not in sessions and calendar and calendar[0] <= d <= calendar[-1]:
            res.off_calendar_signals += sum(1 for s in sigs if s.book in cell.books)

    open_lots: list[Lot] = []
    cash = float(start_equity)
    equity_prev = float(start_equity)
    value_prev = {b: 0.0 for b in cell.books}

    for i, d in enumerate(calendar):
        entered = {b: 0.0 for b in cell.books}
        settled = {b: 0.0 for b in cell.books}

        # 1. settle
        still_open: list[Lot] = []
        for lot in open_lots:
            if lot.exit_date <= d:
                cash += lot.proceeds()
                settled[lot.book] += lot.proceeds()
                if lot.exit_date < d:
                    res.settled_off_calendar += 1
            else:
                still_open.append(lot)
        open_lots = still_open

        # 2. enter -- size off the equity carried in from the previous session
        for sig in signals_by_date.get(d, ()):
            if sig.book not in cell.books:
                continue
            size = cell.size_for(equity_prev)
            if len(open_lots) >= cell.slots:
                res.skipped["slots"] += 1
                continue
            if cash - size < cell.cash_floor or size <= 0:
                res.skipped["cash"] += 1
                continue
            cash -= size
            lot = Lot(sig.book, sig.symbol, d, i, size, sig.exit_date, sig.return_pct, getattr(sig, "side", "long"))
            open_lots.append(lot)
            res.entries.append(lot)
            entered[sig.book] += size
            res.taken += 1
            res.taken_by_book[sig.book] = res.taken_by_book.get(sig.book, 0) + 1

        # 2b. same-day settle (research bench spec 2026-09-12 §3.6): a lot entered today whose
        # exit_date is today closes inside this session -- intraday candidates. Multi-day lots
        # never match, so the existing cells are byte-identical.
        still_open = []
        for lot in open_lots:
            if lot.entry_date == d and lot.exit_date == d:
                cash += lot.proceeds()
                settled[lot.book] += lot.proceeds()
            else:
                still_open.append(lot)
        open_lots = still_open

        # 3. mark
        value = {b: 0.0 for b in cell.books}
        invested = 0.0
        for lot in open_lots:
            ratio = marks.ratio(lot.symbol, lot.entry_i, i)
            if ratio is None:
                res.missing_marks += 1
                res.missing_mark_symbols[lot.symbol] = res.missing_mark_symbols.get(lot.symbol, 0) + 1
                ratio = 1.0                      # unknown price: hold the lot at cost for this session
            # A SHORT lot gains what the price loses: marked at 2 - ratio (floored at zero), so the equity
            # path between entry and settle carries the right sign (amendment 2026-09-14, rsi2-short spec).
            value[lot.book] += lot.size * (ratio if lot.side != "short" else max(0.0, 2.0 - ratio))
            invested += lot.size
        equity = cash + sum(value.values())

        res.dates.append(d)
        res.equity.append(equity)
        res.cash.append(cash)
        res.invested.append(invested)
        res.open_count.append(len(open_lots))
        for b in cell.books:
            # the day's P&L owned by this book: what its lots are worth now, plus what they returned
            # in cash today, less what they were worth last night and what was put into them today.
            res.book_pnl[b].append(value[b] + settled[b] - value_prev[b] - entered[b])
        res.max_open = max(res.max_open, len(open_lots))
        value_prev = value
        equity_prev = equity

    res.open_at_end = len(open_lots)
    return res


def spy_equity(calendar: list[str], marks: Marks, symbol: str = "SPY",
               start_equity: float = START_EQUITY) -> RunResult:
    """The buy-and-hold reference (§4): one lot of the whole book at the first session's adjusted
    close, marked daily like any other lot."""
    res = RunResult(cell=symbol, start_equity=start_equity)
    res.book_pnl = {symbol: []}
    res.taken_by_book = {symbol: 1 if calendar else 0}
    res.taken = 1 if calendar else 0
    prev = 0.0
    for i, d in enumerate(calendar):
        ratio = marks.ratio(symbol, 0, i)
        if ratio is None:
            res.missing_marks += 1
            res.missing_mark_symbols[symbol] = res.missing_mark_symbols.get(symbol, 0) + 1
            ratio = 1.0
        equity = start_equity * ratio
        res.dates.append(d)
        res.equity.append(equity)
        res.cash.append(0.0)
        res.invested.append(start_equity)
        res.open_count.append(1)
        res.book_pnl[symbol].append(equity - prev if i else equity - start_equity)
        prev = equity
    res.max_open = 1 if calendar else 0
    res.open_at_end = 1 if calendar else 0
    return res
