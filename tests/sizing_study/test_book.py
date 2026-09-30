"""The §3 simulator: settle before enter, E_start_of_day, the slot and cash refusals, the fixed order."""
from __future__ import annotations

from pytest import approx

from tests.sizing_study.conftest import D, cell, marks, sig, stream
from webull_api.sizing_study import book
from webull_api.sizing_study.loaders import IBS, RSI2


def test_settle_frees_cash_and_the_slot_before_the_days_entries():
    """Day 2's signal is only affordable -- and only has a slot -- because day 1's lot settled first."""
    c = cell(f=1.0, slots=1)
    sigs = stream(sig(RSI2, "AAA", D[0], D[1], 10.0), sig(RSI2, "BBB", D[1], D[2], 0.0))
    res = book.run(c, D[:3], sigs, marks({"AAA": [100.0, 100.0, 100.0], "BBB": [50.0, 50.0, 50.0]}))
    assert res.taken == 2 and res.skipped == {"slots": 0, "cash": 0}
    assert res.equity[0] == 100_000.0               # the whole book in one lot, marked at cost
    assert res.equity[1] == approx(110_000.0)       # settled +10%, re-entered at the new equity


def test_size_uses_the_equity_carried_in_not_the_post_settle_cash():
    """E_start_of_day is the previous session's mark: a +50% settle on day 2 does not enlarge day 2's lot."""
    c = cell(f=0.5, slots=2)
    sigs = stream(sig(RSI2, "AAA", D[0], D[1], 50.0), sig(RSI2, "BBB", D[1], D[3], 0.0))
    res = book.run(c, D[:3], sigs, marks({"AAA": [100.0] * 3, "BBB": [10.0] * 3}))
    assert res.taken == 2
    assert res.equity[0] == 100_000.0
    assert res.invested[1] == 50_000.0              # 0.5 x 100,000, not 0.5 x the 125,000 post-settle
    assert res.cash[1] == 75_000.0 and res.equity[1] == 125_000.0


def test_full_book_refuses_the_signal_for_a_slot():
    c = cell(f=0.25, slots=1)
    sigs = stream(sig(RSI2, "AAA", D[0], D[3], 0.0), sig(RSI2, "BBB", D[0], D[3], 0.0))
    res = book.run(c, D[:2], sigs, marks({"AAA": [100.0] * 2, "BBB": [100.0] * 2}))
    assert res.taken == 1 and res.skipped["slots"] == 1 and res.skipped["cash"] == 0


def test_short_cash_refuses_the_signal_with_slots_still_free():
    c = cell(f=1.0, slots=3)
    sigs = stream(sig(RSI2, "AAA", D[0], D[3], 0.0), sig(RSI2, "BBB", D[0], D[3], 0.0))
    res = book.run(c, D[:2], sigs, marks({"AAA": [100.0] * 2, "BBB": [100.0] * 2}))
    assert res.taken == 1 and res.skipped["cash"] == 1 and res.skipped["slots"] == 0


def test_b0_style_cash_floor_refuses_an_entry_that_would_breach_it():
    """The B0 reference keeps the backtest's own $20,000 floor: cash - size must clear it."""
    c = cell(f=None, slots=6, fixed_size=6_000.0, cash_floor=20_000.0)
    sigs = stream(sig(RSI2, "AAA", D[0], D[3], 0.0))
    res = book.run(c, D[:2], sigs, marks({"AAA": [100.0] * 2}), start_equity=25_000.0)
    assert res.taken == 0 and res.skipped["cash"] == 1


def test_entries_run_rsi2_before_ibs_then_by_symbol():
    c = cell(f=1.0, slots=1, books=(RSI2, IBS))
    sigs = stream(sig(IBS, "AAA", D[0], D[3], 0.0), sig(RSI2, "ZZZ", D[0], D[3], 0.0))
    res = book.run(c, D[:2], sigs, marks({"AAA": [100.0] * 2, "ZZZ": [100.0] * 2}))
    assert res.taken == 1 and res.taken_by_book == {RSI2: 1, IBS: 0}

    c2 = cell(f=1.0, slots=1)
    sigs2 = stream(sig(RSI2, "ZZZ", D[0], D[3], 0.0), sig(RSI2, "AAA", D[0], D[3], 0.0))
    res2 = book.run(c2, D[:2], sigs2, marks({"AAA": [100.0] * 2, "ZZZ": [100.0] * 2}))
    assert res2.taken == 1 and [l.symbol for l in res2.entries] == ["AAA"]   # by symbol within a book


def test_open_lots_are_marked_at_adjusted_closes_and_the_series_adds_up():
    c = cell(f=0.5, slots=2)
    sigs = stream(sig(RSI2, "AAA", D[0], D[2], 20.0))
    res = book.run(c, D[:3], sigs, marks({"AAA": [100.0, 120.0, 200.0]}))
    assert res.equity[0] == 100_000.0                 # 50,000 cash + 50,000 at cost
    assert res.equity[1] == approx(110_000.0)          # the lot marked at 120/100
    assert res.equity[2] == approx(110_000.0)          # settled at the trade's own +20%, mark ignored
    assert res.cash[2] == approx(110_000.0) and res.open_at_end == 0


def test_an_unknown_price_holds_the_lot_at_cost_and_is_counted():
    c = cell(f=0.5, slots=2)
    sigs = stream(sig(RSI2, "NEW", D[1], D[3], 0.0))
    res = book.run(c, D[:3], sigs, marks({"NEW": [None, None, None]}))
    assert res.missing_marks == 2 and res.missing_mark_symbols == {"NEW": 2}
    assert res.equity[2] == 100_000.0


def test_book_pnl_splits_the_days_change_between_the_two_books():
    c = cell(f=0.5, slots=2, books=(RSI2, IBS))
    sigs = stream(sig(RSI2, "AAA", D[0], D[3], 0.0), sig(IBS, "BBB", D[0], D[3], 0.0))
    res = book.run(c, D[:3], sigs, marks({"AAA": [100.0, 110.0, 110.0], "BBB": [100.0, 100.0, 90.0]}))
    assert res.book_pnl[RSI2][1] == approx(5_000.0) and res.book_pnl[IBS][1] == approx(0.0)
    assert res.book_pnl[IBS][2] == approx(-5_000.0)
    for i in range(1, 3):
        assert round(res.equity[i] - res.equity[i - 1], 6) == round(
            res.book_pnl[RSI2][i] + res.book_pnl[IBS][i], 6)


def test_spy_reference_is_buy_and_hold_at_adjusted_closes():
    res = book.spy_equity(D[:3], marks({"SPY": [100.0, 110.0, 90.0]}), "SPY")
    assert res.equity == approx([100_000.0, 110_000.0, 90_000.0])
    assert res.taken == 1 and res.invested[0] == 100_000.0


def test_a_short_lot_is_marked_at_two_minus_ratio_and_settles_on_its_own_return():
    # amendment 2026-09-14 (rsi2-short spec): an open SHORT lot gains what the price loses
    from webull_api.sizing_study.loaders import Signal
    c = cell(f=0.5, slots=2)
    sigs = stream(Signal(RSI2, "AAA", D[0], D[2], 20.0, "short"))
    res = book.run(c, D[:3], sigs, marks({"AAA": [100.0, 80.0, 200.0]}))
    assert res.equity[0] == 100_000.0                 # 50,000 cash + 50,000 at cost
    assert res.equity[1] == approx(110_000.0)          # price 100 -> 80: the short lot is marked at 2 - 0.8 = 1.2
    assert res.equity[2] == approx(110_000.0)          # settled at the trade's own +20%, mark ignored
    # a price that doubles against a short marks the lot at zero, never negative
    res2 = book.run(c, D[:3], stream(Signal(RSI2, "AAA", D[0], D[2], -50.0, "short")), marks({"AAA": [100.0, 250.0, 100.0]}))
    assert res2.equity[1] == approx(50_000.0) and res2.equity[2] == approx(75_000.0)
    # the default side is long and long cells are byte-identical to before
    assert Signal(RSI2, "AAA", D[0], D[2], 20.0).side == "long"
