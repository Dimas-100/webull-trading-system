"""Cycle-start bar-sanity preflight — the generalization of the 2026-08-15 reversed-bars
lesson: 31 cycles screened silently corrupted data because nothing checked the feed's shape
before Gate A consumed it. The preflight is pure; the lab_service shell refuses to screen
(no M consumed) when it reports problems."""
from webull_api.lab import preflight


def _bars(dates, *, o=10.0, h=11.0, low=9.0, c=10.5, v=100):
    return [{"time": d, "open": o, "high": h, "low": low, "close": c, "volume": v}
            for d in dates]


CLEAN = {"SPY": _bars(["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]),
         "AAPL": _bars(["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"])}


def test_clean_recent_bars_pass():
    assert preflight.check_bars(CLEAN, today_et="2026-08-16") == []


def test_reversed_bars_are_caught():
    # THE regression: time-descending bars fed Gate A for 31 cycles undetected.
    bad = {"SPY": _bars(["2026-08-14", "2026-08-13", "2026-08-12", "2026-08-11"])}
    problems = preflight.check_bars(bad, today_et="2026-08-16")
    assert any("SPY" in p and "ascending" in p for p in problems)


def test_duplicate_dates_are_caught():
    bad = {"SPY": _bars(["2026-08-13", "2026-08-14", "2026-08-14"])}
    assert preflight.check_bars(bad, today_et="2026-08-16") != []


def test_stale_last_bar_is_caught():
    stale = {"SPY": _bars(["2026-08-01", "2026-08-02", "2026-08-03"])}
    problems = preflight.check_bars(stale, today_et="2026-08-16")
    assert any("SPY" in p and "stale" in p for p in problems)


def test_weekend_and_holiday_gaps_are_not_stale():
    # Monday after a long weekend: last bar Thursday = 4 calendar days back -> clean.
    friday_tail = {"SPY": _bars(["2026-08-12", "2026-08-13"])}
    assert preflight.check_bars(friday_tail, today_et="2026-08-17") == []


def test_incoherent_ohlc_is_caught():
    bad = {"SPY": [{"time": "2026-08-14", "open": 10.0, "high": 9.0, "low": 11.0,
                    "close": 10.5, "volume": 100}]}
    problems = preflight.check_bars(bad, today_et="2026-08-16")
    assert any("SPY" in p for p in problems)


def test_non_positive_price_is_caught():
    bad = {"SPY": [{"time": "2026-08-14", "open": 0.0, "high": 11.0, "low": 9.0,
                    "close": 10.5, "volume": 100}]}
    assert preflight.check_bars(bad, today_et="2026-08-16") != []


def test_empty_symbol_series_is_caught():
    assert preflight.check_bars({"SPY": []}, today_et="2026-08-16") != []


def test_one_bad_symbol_does_not_mask_another():
    mixed = dict(CLEAN)
    mixed["BAD"] = _bars(["2026-08-14", "2026-08-13"])
    problems = preflight.check_bars(mixed, today_et="2026-08-16")
    assert any("BAD" in p for p in problems)
    assert not any("SPY" in p for p in problems)


def test_short_history_lists_symbols_below_min_bars_sorted_by_symbol():
    bars = {"TSLA": _bars(["2026-08-13"]),
            "LIN": _bars(["2026-08-12", "2026-08-13"]),
            "SPY": _bars(["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14", "2026-08-15"])}
    assert preflight.short_history(bars, min_bars=5) == [("LIN", 2), ("TSLA", 1)]


def test_short_history_empty_when_every_symbol_is_long_enough():
    assert preflight.short_history(CLEAN, min_bars=4) == []


def test_short_history_empty_for_an_empty_dict():
    assert preflight.short_history({}, min_bars=750) == []


def _series(*dates):
    return [{"time": d, "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1} for d in dates]


def test_fresh_bar_day_needs_a_bar_dated_today_in_at_least_one_symbol():
    today = "2026-09-08"
    assert preflight.fresh_bar_day({"SPY": _series("2026-09-04", today)}, today_et=today)
    # Labor Day: the feed is recent (passes check_bars) but nothing is dated today
    assert not preflight.fresh_bar_day({"SPY": _series("2026-09-03", "2026-09-04")}, today_et="2026-09-07")
    assert not preflight.check_bars({"SPY": _series("2026-09-03", "2026-09-04")}, today_et="2026-09-07")
    # one fresh symbol is enough; empty and unparseable series are not fresh
    assert preflight.fresh_bar_day({"A": _series("2026-09-04"), "B": _series(today)}, today_et=today)
    assert not preflight.fresh_bar_day({"A": []}, today_et=today)
    assert not preflight.fresh_bar_day({"A": [{"time": "garbage"}]}, today_et=today)
    assert not preflight.fresh_bar_day({}, today_et=today)
