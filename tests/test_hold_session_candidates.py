"""Round-3 candidate rules (spec 2026-09-09-hold-round-design.md §3): pure, synthetic bars only."""
from datetime import date, timedelta

import pytest

from webull_api.hold_session.candidates import CANDIDATES, Candidate, Pick, gap, ibs, rsi2_at, signals
from webull_api.hold_session.series import Series
from webull_api.strategy.rsi2 import UNIVERSE, rsi2_of


def row(date, o, h, l, c, div=0.0, split=1.0):
    return {"date": date, "open": o, "high": h, "low": l, "close": c, "volume": 1000.0,
            "adj_open": o, "adj_high": h, "adj_low": l, "adj_close": c, "adj_volume": 1000.0,
            "div_cash": div, "split_factor": split}


def dates(n):
    d0 = date(2024, 1, 2)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def falling(sym, n=40, start=100.0, step=1.0):
    ds = dates(n)
    rows = [row(d, start - i * step + 0.5, start - i * step + 1.0, start - i * step - 0.5, start - i * step)
            for i, d in enumerate(ds)]
    return Series(sym, rows), ds


def test_series_lookup_and_windows():
    s, ds = falling("AAPL", n=5)
    assert s.row(ds[2])["date"] == ds[2]
    assert s.row("1999-01-01") is None
    assert s.prev_row(ds[0]) is None and s.prev_row(ds[3])["date"] == ds[2]
    assert s.adj_closes_through(ds[4], 3) == [98.0, 97.0, 96.0]
    assert s.adj_closes_through(ds[1], 3) == []
    assert s.adj_closes_through("1999-01-01", 1) == []
    unsorted = Series("X", [row(ds[1], 1, 1, 1, 1), row(ds[0], 2, 2, 2, 2)])
    assert [r["date"] for r in unsorted.rows] == [ds[0], ds[1]]


def test_registry_matches_the_spec_table():
    assert set(CANDIDATES) == {"H1", "G1", "R1", "R2"}
    h1, g1, r1, r2 = (CANDIDATES[k] for k in ("H1", "G1", "R1", "R2"))
    assert (h1.rule, h1.entry, h1.exit, h1.signal_symbol, h1.exec_symbol, h1.threshold) == \
        ("ibs", "D.close", "D1.close", "SPY", "SPLG", 0.2)
    assert (g1.rule, g1.entry, g1.exit, g1.max_rank, g1.universe) == ("gap", "D.open", "D.close", 3, UNIVERSE)
    assert (r1.rule, r1.entry, r1.exit, r1.threshold, r1.universe) == ("rsi2", "D.close", "D1.open", 10.0, UNIVERSE)
    assert (r2.rule, r2.entry, r2.exit, r2.threshold) == ("rsi2", "D1.open", "D1.close", 10.0)
    assert h1.crosses_night and r1.crosses_night
    assert not g1.crosses_night and not r2.crosses_night


def test_ibs_arithmetic_and_zero_range():
    assert ibs(row("2024-01-02", 10, 12, 8, 8.4)) == pytest.approx(0.1)
    assert ibs(row("2024-01-02", 10, 12, 8, 12)) == pytest.approx(1.0)
    assert ibs(row("2024-01-02", 10, 10, 10, 10)) is None


def test_gap_is_adjusted_open_over_prior_adjusted_close():
    prev = row("2024-01-02", 10, 11, 9, 10.0)
    today = row("2024-01-03", 9.8, 10, 9.5, 9.9)
    assert gap(today, prev) == pytest.approx(-0.02)


def test_h1_signals_below_threshold_and_names_the_exec_symbol():
    spy = Series("SPY", [row("2024-01-02", 10, 12, 8, 8.4), row("2024-01-03", 10, 12, 8, 11)])
    s = {"SPY": spy}
    picks = signals(CANDIDATES["H1"], "2024-01-02", None, s)
    assert [p.symbol for p in picks] == ["SPLG"]
    assert picks[0].value == pytest.approx(0.1)
    assert signals(CANDIDATES["H1"], "2024-01-03", "2024-01-02", s) == []
    assert signals(CANDIDATES["H1"], "2024-01-04", "2024-01-03", s) == []
    assert signals(CANDIDATES["H1"], "2024-01-02", None, {}) == []


def test_g1_ranks_bottom_three_ascending_with_symbol_tiebreak_and_needs_the_prior_session():
    ds = ["2024-01-02", "2024-01-03"]

    def two(sym, o2):
        return Series(sym, [row(ds[0], 10, 11, 9, 10.0), row(ds[1], o2, o2 + 1, o2 - 1, o2)])

    s = {"AAPL": two("AAPL", 9.5), "MSFT": two("MSFT", 9.8), "NVDA": two("NVDA", 9.8),
         "AMZN": two("AMZN", 10.5), "GOOG": two("GOOG", 9.0)}
    picks = signals(CANDIDATES["G1"], ds[1], ds[0], s)
    assert [p.symbol for p in picks] == ["GOOG", "AAPL", "MSFT"]
    assert picks[0].value == pytest.approx(-0.10)
    assert signals(CANDIDATES["G1"], ds[1], "2024-01-01", s) == []
    assert signals(CANDIDATES["G1"], ds[1], None, s) == []
    assert signals(CANDIDATES["G1"], ds[0], None, s) == []


def test_rsi2_at_matches_production_over_the_30_close_window():
    series, ds = falling("AAPL", n=40)
    closes = [r["adj_close"] for r in series.rows]
    assert rsi2_at(series, ds[-1]) == pytest.approx(rsi2_of(closes[-30:]))
    assert rsi2_at(series, ds[10]) is None
    assert rsi2_at(series, "1999-01-01") is None


def test_r1_r2_signal_below_ten_most_oversold_first():
    fall, ds = falling("AAPL", n=40)
    slow, _ = falling("MSFT", n=40, step=0.1)
    flat = Series("NVDA", [row(d, 50, 51, 49, 50) for d in ds])
    s = {"AAPL": fall, "MSFT": slow, "NVDA": flat}
    picks = signals(CANDIDATES["R1"], ds[-1], ds[-2], s)
    assert [p.symbol for p in picks] == ["AAPL", "MSFT"]
    assert all(p.value < 10 for p in picks)
    assert signals(CANDIDATES["R2"], ds[-1], ds[-2], s) == picks
    assert signals(CANDIDATES["R1"], ds[5], ds[4], s) == []


def test_unknown_rule_raises():
    with pytest.raises(ValueError):
        signals(Candidate("X", "nope", "D.close", "D1.close"), "2024-01-02", None, {})


def test_candidate_rejects_entry_or_exit_outside_the_registry():
    """A typo'd session token in a future candidate must fail at construction, not silently produce a
    plausible-looking wrong fill (I-4: _resolve used to map any non-'D' token to next_d)."""
    with pytest.raises(ValueError):
        Candidate("X", "rsi2", "D2.close", "D1.close")   # bad entry
    with pytest.raises(ValueError):
        Candidate("X", "rsi2", "D.close", "D2.close")    # bad exit
    Candidate("X", "rsi2", "D.close", "D1.close")        # valid: does not raise
