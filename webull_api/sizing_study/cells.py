"""The declared cells (spec §4). Nothing here is discovered by a run -- every cell, its books,
its fraction of equity per lot and its slot count were pre-registered before the develop window ran."""
from __future__ import annotations

from dataclasses import dataclass

from webull_api.sizing_study.loaders import IBS, RSI2

START_EQUITY = 100_000.0      # §3: "Equity E starts at 100,000 (units are arbitrary)"
DD_BUDGET_PCT = 30.0          # §6: the declared drawdown budget for the selection rule
B0 = "B0"
SPY = "SPY"


@dataclass(frozen=True)
class Cell:
    """One book configuration. ``f`` sizes a lot as E_start_of_day x f; ``fixed_size`` (B0 only)
    sizes it in dollars instead. A signal is refused when ``cash - size < cash_floor`` (with the
    default floor of 0 that is §3's "cash < size") or when open lots >= ``slots``."""
    id: str
    books: tuple[str, ...]
    f: float | None
    slots: int
    label: str
    fixed_size: float | None = None
    cash_floor: float = 0.0
    reference: bool = False

    def size_for(self, equity_start_of_day: float) -> float:
        return self.fixed_size if self.fixed_size is not None else equity_start_of_day * float(self.f)


CELLS: dict[str, Cell] = {
    "R8": Cell("R8", (RSI2,), 1 / 8, 8, "RSI2 · f=1/8"),
    "R6": Cell("R6", (RSI2,), 1 / 6, 6, "RSI2 · f=1/6"),
    "R4": Cell("R4", (RSI2,), 1 / 4, 4, "RSI2 · f=1/4"),
    "S8": Cell("S8", (RSI2, IBS), 1 / 8, 8, "RSI2 + IBS · f=1/8"),
    "S6": Cell("S6", (RSI2, IBS), 1 / 6, 6, "RSI2 + IBS · f=1/6"),
    "S4": Cell("S4", (RSI2, IBS), 1 / 4, 4, "RSI2 + IBS · f=1/4"),
    "I4": Cell("I4", (IBS,), 1 / 4, 4, "IBS · f=1/4"),
    B0: Cell(B0, (RSI2,), None, 6, "B0 reference · RSI2 at the backtest's own rule",
             fixed_size=6_000.0, cash_floor=20_000.0, reference=True),
}

ORDER = ["R8", "R6", "R4", "S8", "S6", "S4", "I4", B0]
SELECTABLE = [c for c in ORDER if not CELLS[c].reference]   # §6 selects among the seven sized cells


def stacked(cell_id: str) -> bool:
    return len(CELLS[cell_id].books) > 1
