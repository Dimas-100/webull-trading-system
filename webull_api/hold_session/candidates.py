"""Round-3 candidate rules (spec §3). Signals on adjusted prices; the replay fills on raw prices.

Entry/exit keys are relative to the signal session D: "D.open", "D.close", "D1.open", "D1.close"."""
from __future__ import annotations

from dataclasses import dataclass

from webull_api.hold_session.series import Series
from webull_api.strategy.rsi2 import DEFAULT_CONFIG, UNIVERSE, rsi2_of

RSI_WINDOW = 30          # production evaluates RSI(2) over a trailing 30-close window
IBS_ENTRY = 0.2
IBS_STRONG = 0.8         # used by diagnostic D2 and, since round 4's D3, hold_until_strong_book's exit test
GAP_MAX_RANK = 3         # G1 looks no deeper than the bottom three of 28 (about a decile)

# Round 4 (spec 2026-09-10-ibs-book-design.md §3): SPY + the eleven sector SPDRs. Disjoint from the swing
# book's universe BY INVARIANT — the check below fails the import if anyone ever adds an overlapping name.
# A plain `assert` is stripped under `python -O`; raise unconditionally so the invariant always holds.
IBS_ETF_UNIVERSE = ("SPY", "XLK", "XLP", "XLF", "XLE", "XLV", "XLI", "XLU", "XLY", "XLB", "XLC", "XLRE")
if set(IBS_ETF_UNIVERSE) & set(UNIVERSE):
    raise ImportError("IBS_ETF_UNIVERSE overlaps the RSI(2) swing universe")
IBS_BOOK_SLOTS = 4

ENTRIES = ("D.open", "D.close", "D1.open")
HOLDS = ("one_session", "until_ibs_strong")   # round 5: how long a book lot is held
EXITS = ("D.close", "D1.open", "D1.close")


@dataclass(frozen=True)
class Candidate:
    id: str
    rule: str                              # "ibs" | "gap" | "rsi2" | "ibs_self"
    entry: str                             # one of ENTRIES
    exit: str                              # one of EXITS
    signal_symbol: str | None = None       # H1: SPY
    exec_symbol: str | None = None         # H1: SPLG
    universe: tuple[str, ...] = ()
    max_rank: int | None = None
    threshold: float | None = None
    max_slots: int | None = None           # round 4: concurrent lots the book may hold
    hold: str = "one_session"              # round 5: "one_session" (exit next close) | "until_ibs_strong"

    def __post_init__(self) -> None:
        if self.entry not in ENTRIES:
            raise ValueError(f"{self.id}: entry {self.entry!r} not in ENTRIES {ENTRIES}")
        if self.exit not in EXITS:
            raise ValueError(f"{self.id}: exit {self.exit!r} not in EXITS {EXITS}")
        if self.hold not in HOLDS:
            raise ValueError(f"{self.id}: hold {self.hold!r} not in HOLDS {HOLDS}")

    @property
    def crosses_night(self) -> bool:
        return self.entry.startswith("D.") and self.exit.startswith("D1.")


@dataclass(frozen=True)
class Pick:
    symbol: str      # the symbol to trade
    value: float     # the ranking value: IBS, gap, or RSI(2)
    reason: str


CANDIDATES: dict[str, Candidate] = {
    "H1": Candidate("H1", "ibs", "D.close", "D1.close", signal_symbol="SPY", exec_symbol="SPLG",
                    threshold=IBS_ENTRY),
    "G1": Candidate("G1", "gap", "D.open", "D.close", universe=UNIVERSE, max_rank=GAP_MAX_RANK),
    "R1": Candidate("R1", "rsi2", "D.close", "D1.open", universe=UNIVERSE, threshold=DEFAULT_CONFIG.entry_below),
    "R2": Candidate("R2", "rsi2", "D1.open", "D1.close", universe=UNIVERSE, threshold=DEFAULT_CONFIG.entry_below),
}

IBS_BOOK_CANDIDATES: dict[str, Candidate] = {
    "B1": Candidate("B1", "ibs_self", "D.close", "D1.close", universe=IBS_ETF_UNIVERSE,
                    max_slots=IBS_BOOK_SLOTS, threshold=IBS_ENTRY),
    # Round 5 (spec 2026-09-10-ibs-round5-design.md): same entry, hold each lot until ITS OWN IBS closes
    # above IBS_STRONG. `exit="D1.close"` is the earliest possible exit (a lot never exits the close it
    # was bought on); the `hold` field governs the actual exit.
    "B2": Candidate("B2", "ibs_self", "D.close", "D1.close", universe=IBS_ETF_UNIVERSE,
                    max_slots=IBS_BOOK_SLOTS, threshold=IBS_ENTRY, hold="until_ibs_strong"),
}


def ibs(row: dict) -> float | None:
    """(close - low) / (high - low) on the adjusted bar; None when the day's range is zero."""
    rng = row["adj_high"] - row["adj_low"]
    if rng <= 0:
        return None
    return (row["adj_close"] - row["adj_low"]) / rng


def gap(row: dict, prev: dict) -> float:
    """Overnight return: today's adjusted open over the prior session's adjusted close, minus one."""
    return row["adj_open"] / prev["adj_close"] - 1.0


def rsi2_at(series: Series, d: str) -> float | None:
    """Production RSI(2) at the close of d over exactly RSI_WINDOW closes; None without a full window."""
    closes = series.adj_closes_through(d, RSI_WINDOW)
    return rsi2_of(closes) if len(closes) == RSI_WINDOW else None


def signals(cand: Candidate, d: str, prev_d: str | None, series_by_symbol: dict[str, Series]) -> list[Pick]:
    """Ranked picks for signal session d, best first; [] when nothing signals."""
    if cand.rule == "ibs":
        s = series_by_symbol.get(cand.signal_symbol)
        row = s.row(d) if s is not None else None
        if row is None:
            return []
        v = ibs(row)
        if v is None or v >= cand.threshold:
            return []
        return [Pick(cand.exec_symbol, v, f"IBS({cand.signal_symbol})={v:.3f} < {cand.threshold:g}")]
    if cand.rule == "gap":
        picks: list[Pick] = []
        if prev_d is None:
            return picks
        for sym in cand.universe:
            s = series_by_symbol.get(sym)
            row = s.row(d) if s is not None else None
            prev = s.prev_row(d) if row is not None else None
            if row is None or prev is None or prev["date"] != prev_d:
                continue
            g = gap(row, prev)
            picks.append(Pick(sym, g, f"gap={g * 100:+.2f}%"))
        picks.sort(key=lambda p: (p.value, p.symbol))
        return picks[: cand.max_rank]
    if cand.rule == "rsi2":
        picks = []
        for sym in cand.universe:
            s = series_by_symbol.get(sym)
            if s is None or s.row(d) is None:
                continue
            r = rsi2_at(s, d)
            if r is not None and r < cand.threshold:
                picks.append(Pick(sym, r, f"RSI(2)={r:.2f} < {cand.threshold:g}"))
        picks.sort(key=lambda p: (p.value, p.symbol))
        return picks
    if cand.rule == "ibs_self":
        picks = []
        for sym in cand.universe:
            s = series_by_symbol.get(sym)
            row = s.row(d) if s is not None else None
            if row is None:
                continue
            v = ibs(row)
            if v is None or v >= cand.threshold:
                continue
            picks.append(Pick(sym, v, f"IBS={v:.3f} < {cand.threshold:g}"))
        picks.sort(key=lambda p: (p.value, p.symbol))
        return picks
    raise ValueError(f"unknown rule {cand.rule!r}")
