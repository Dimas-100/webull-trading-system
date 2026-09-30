"""Synthetic fixtures for the sizing study: no real Tiingo store, no report files, no clock."""
from __future__ import annotations

import pytest

from webull_api.sizing_study.cells import Cell
from webull_api.sizing_study.loaders import IBS, RSI2, Marks, Signal, by_entry_date

D = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08"]


def marks(prices: dict[str, list[float | None]], calendar=None) -> Marks:
    """Build marks straight from per-symbol adj_close lists aligned to the calendar."""
    cal = calendar or D[:len(next(iter(prices.values())))]
    rows = {sym: [{"date": d, "adj_close": p} for d, p in zip(cal, series) if p is not None]
            for sym, series in prices.items()}
    return Marks(cal, rows)


def sig(book, symbol, entry, exit_, ret) -> Signal:
    return Signal(book, symbol, entry, exit_, ret)


def stream(*signals) -> dict:
    return by_entry_date(list(signals))


def cell(f=1.0, slots=1, books=(RSI2,), **kw) -> Cell:
    return Cell("T", tuple(books), f, slots, "test cell", **kw)


@pytest.fixture
def calendar():
    return list(D)


@pytest.fixture
def both_books():
    return (RSI2, IBS)
