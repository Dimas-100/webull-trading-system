"""Round-4 candidate rule and registry (spec 2026-09-10-ibs-book-design.md §1, §3): synthetic bars only."""
import pytest

from webull_api.hold_session.candidates import (CANDIDATES, IBS_BOOK_CANDIDATES, IBS_ETF_UNIVERSE,
                                                Candidate, signals)
from webull_api.hold_session.series import Series
from webull_api.strategy.rsi2 import UNIVERSE


def row(date, o, h, l, c, div=0.0, split=1.0):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0,
            "adj_open": o, "adj_high": h, "adj_low": l, "adj_close": c, "adj_volume": 1000.0,
            "div_cash": div, "split_factor": split}


def test_etf_universe_is_disjoint_from_the_swing_universe_and_has_twelve_names():
    assert not set(IBS_ETF_UNIVERSE) & set(UNIVERSE)
    assert len(IBS_ETF_UNIVERSE) == 12 and len(set(IBS_ETF_UNIVERSE)) == 12
    assert IBS_ETF_UNIVERSE[0] == "SPY"
    assert set(IBS_ETF_UNIVERSE) == {"SPY", "XLK", "XLP", "XLF", "XLE", "XLV", "XLI", "XLU", "XLY", "XLB", "XLC", "XLRE"}


def test_b1_registry_matches_the_spec_and_round_3_registry_is_untouched():
    b = IBS_BOOK_CANDIDATES["B1"]
    assert (b.id, b.rule, b.entry, b.exit) == ("B1", "ibs_self", "D.close", "D1.close")
    assert b.universe == IBS_ETF_UNIVERSE and b.max_slots == 4 and b.threshold == 0.2
    assert b.crosses_night
    assert set(IBS_BOOK_CANDIDATES) == {"B1", "B2"}   # B2 added in round 5
    assert set(CANDIDATES) == {"H1", "G1", "R1", "R2"}
    assert CANDIDATES["H1"].max_slots is None


def test_ibs_self_ranks_deepest_first_with_symbol_tiebreak_and_skips_zero_range_and_missing():
    d = "2024-01-02"
    s = {"SPY": Series("SPY", [row(d, 10, 12, 8, 8.4)]),     # IBS 0.10
         "XLK": Series("XLK", [row(d, 10, 12, 8, 8.0)]),     # IBS 0.00
         "XLP": Series("XLP", [row(d, 10, 12, 8, 8.4)]),     # IBS 0.10 -> ties SPY, SPY sorts first
         "XLF": Series("XLF", [row(d, 10, 12, 8, 11.0)]),    # IBS 0.75, no
         "XLE": Series("XLE", [row(d, 10, 10, 10, 10.0)])}   # zero range, skipped
    picks = signals(IBS_BOOK_CANDIDATES["B1"], d, None, s)
    assert [p.symbol for p in picks] == ["XLK", "SPY", "XLP"]
    assert picks[0].value == pytest.approx(0.0) and picks[1].value == pytest.approx(0.1)
    assert picks[0].reason.startswith("IBS=")
    assert signals(IBS_BOOK_CANDIDATES["B1"], "2024-01-03", d, s) == []
    assert signals(IBS_BOOK_CANDIDATES["B1"], d, None, {}) == []


def test_ibs_self_threshold_is_strict():
    d = "2024-01-02"
    s = {"SPY": Series("SPY", [row(d, 10, 12, 8, 8.8)])}      # IBS exactly 0.2 -> not below
    assert signals(IBS_BOOK_CANDIDATES["B1"], d, None, s) == []


def test_candidate_max_slots_defaults_none_and_unknown_rule_still_raises():
    c = Candidate("X", "ibs_self", "D.close", "D1.close", universe=("SPY",), threshold=0.2)
    assert c.max_slots is None
    with pytest.raises(ValueError):
        signals(Candidate("Y", "nope", "D.close", "D1.close"), "2024-01-02", None, {})
