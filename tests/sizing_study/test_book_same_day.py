"""book.run settles a same-day lot inside its own session (bench spec §3.6) and leaves multi-day lots alone."""
from webull_api.sizing_study import book
from webull_api.sizing_study.cells import Cell
from webull_api.sizing_study.loaders import Marks, Signal, by_entry_date

CAL = ["2024-01-02", "2024-01-03", "2024-01-04"]


def _marks():
    rows = [{"date": d, "adj_close": 100.0} for d in CAL]
    return Marks(CAL, {"SPY": rows})


def _cell():
    return Cell("t", ("cand",), 0.5, 2, "test")


def test_same_day_lot_settles_in_session_and_frees_its_slot():
    sigs = by_entry_date([Signal("cand", "SPY", "2024-01-02", "2024-01-02", 2.0)])
    res = book.run(_cell(), CAL, sigs, _marks(), 1000.0)
    assert res.open_count == [0, 0, 0]
    assert res.equity[0] == 1010.0                       # 500 x 2% settled the same day
    assert res.book_pnl["cand"][0] == 10.0 and res.settled_off_calendar == 0


def test_multi_day_lot_unchanged():
    sigs = by_entry_date([Signal("cand", "SPY", "2024-01-02", "2024-01-03", 2.0)])
    res = book.run(_cell(), CAL, sigs, _marks(), 1000.0)
    assert res.open_count == [1, 0, 0]
    assert res.equity == [1000.0, 1010.0, 1010.0]
