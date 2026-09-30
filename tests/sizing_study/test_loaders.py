"""§2 inputs: the two trade lists become one dated, fixed-order signal stream; marks carry forward."""
from __future__ import annotations

from webull_api.sizing_study.cells import CELLS, DD_BUDGET_PCT, SELECTABLE, START_EQUITY
from webull_api.sizing_study.loaders import IBS, RSI2, Marks, by_entry_date, signals_from


def test_both_lists_normalise_to_the_same_signal_despite_different_price_keys():
    rsi2 = [{"symbol": "AAPL", "entry_date": "2020-01-02", "entry_price": 100.0,
             "exit_date": "2020-01-07", "exit_price": 104.0, "return_pct": 4.0}]
    ibs = [{"symbol": "XLK", "entry_date": "2020-01-02", "entry": 50.0, "exit_date": "2020-01-03",
            "exit": 51.0, "return_pct": 2.0}]
    sigs = signals_from(rsi2, ibs)
    assert [(s.book, s.symbol, s.return_pct) for s in sigs] == [
        (RSI2, "AAPL", 4.0), (IBS, "XLK", 2.0)]
    assert sigs[0].exit_date == "2020-01-07"


def test_the_stream_is_ordered_rsi2_before_ibs_then_by_symbol():
    rsi2 = [{"symbol": "ZZZ", "entry_date": "2020-01-02", "exit_date": "2020-01-03", "return_pct": 0.0},
            {"symbol": "AAA", "entry_date": "2020-01-02", "exit_date": "2020-01-03", "return_pct": 0.0}]
    ibs = [{"symbol": "AAB", "entry_date": "2020-01-02", "exit_date": "2020-01-03", "return_pct": 0.0}]
    day = by_entry_date(signals_from(rsi2, ibs))["2020-01-02"]
    assert [(s.book, s.symbol) for s in day] == [(RSI2, "AAA"), (RSI2, "ZZZ"), (IBS, "AAB")]


def test_a_row_without_a_date_or_a_return_is_dropped_rather_than_guessed():
    rows = [{"symbol": "AAA", "entry_date": "2020-01-02", "exit_date": None, "return_pct": 1.0},
            {"symbol": "BBB", "entry_date": "2020-01-02", "exit_date": "2020-01-03", "return_pct": None}]
    assert signals_from(rows, []) == []


def test_marks_carry_the_last_close_forward_over_a_symbol_holiday():
    cal = ["2020-01-02", "2020-01-03", "2020-01-06"]
    m = Marks(cal, {"AAA": [{"date": "2020-01-02", "adj_close": 100.0},
                            {"date": "2020-01-06", "adj_close": 120.0}]})
    assert m.ratio("AAA", 0, 1) == 1.0          # no row on the 3rd: held at the last known close
    assert m.ratio("AAA", 0, 2) == 1.2
    assert m.ratio("AAA", 0, 5) is None and m.ratio("ZZZ", 0, 1) is None


def test_marks_before_a_symbols_first_row_are_unknown_not_zero():
    cal = ["2020-01-02", "2020-01-03"]
    m = Marks(cal, {"NEW": [{"date": "2020-01-03", "adj_close": 10.0}]})
    assert m.value["NEW"] == [None, 10.0] and m.ratio("NEW", 0, 1) is None


def test_the_declared_cells_match_the_spec_table():
    assert [c for c in CELLS if c != "B0"] == ["R8", "R6", "R4", "S8", "S6", "S4", "I4"]
    for cid in SELECTABLE:
        cell = CELLS[cid]
        assert cell.slots == round(1 / cell.f)            # §3: N = round(1/f)
        assert cell.fixed_size is None and cell.cash_floor == 0.0 and not cell.reference
    assert [CELLS[c].books for c in ("R8", "S8", "I4")] == [(RSI2,), (RSI2, IBS), (IBS,)]
    assert [CELLS[c].f for c in ("R8", "R6", "R4")] == [1 / 8, 1 / 6, 1 / 4]
    b0 = CELLS["B0"]
    assert (b0.fixed_size, b0.slots, b0.cash_floor, b0.f, b0.reference) == (6000.0, 6, 20000.0, None, True)
    assert START_EQUITY == 100_000.0 and DD_BUDGET_PCT == 30.0


def test_size_for_scales_with_equity_except_on_the_fixed_lot_reference():
    assert CELLS["R4"].size_for(200_000.0) == 50_000.0
    assert CELLS["B0"].size_for(200_000.0) == 6_000.0
