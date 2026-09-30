"""One symbol's daily rows with O(1) lookup by date. Rows are daily store dicts: date, open, high, low,
close, adj_open, adj_high, adj_low, adj_close, div_cash, split_factor."""
from __future__ import annotations


class Series:
    def __init__(self, symbol: str, rows: list[dict]):
        self.symbol = symbol
        self.rows = sorted(rows, key=lambda r: r["date"])
        self._idx = {r["date"]: i for i, r in enumerate(self.rows)}

    def row(self, d: str) -> dict | None:
        i = self._idx.get(d)
        return self.rows[i] if i is not None else None

    def prev_row(self, d: str) -> dict | None:
        """The symbol's own row just before date d; None when d is absent or first."""
        i = self._idx.get(d)
        return self.rows[i - 1] if i is not None and i > 0 else None

    def adj_closes_through(self, d: str, n: int) -> list[float]:
        """The last n adj_close values ending at d inclusive; [] when d is absent or fewer than n rows exist."""
        i = self._idx.get(d)
        if i is None or i + 1 < n:
            return []
        return [r["adj_close"] for r in self.rows[i + 1 - n: i + 1]]
