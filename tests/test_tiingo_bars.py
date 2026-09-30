"""The seam the 2026-08-15 reversed-bars incident taught us to test: adapter output is oldest-first,
time = YYYY-MM-DD, idempotent through to_ohlcv, adjusted by default, count trims the newest end."""
import pytest

from webull_api.strategy.bars import to_ohlcv
from webull_api.tiingo import bars as mod


def _row(d, px, adj, split=1.0, div=0.0):
    return {"date": d, "open": px, "high": px + 1, "low": px - 1, "close": px + 0.5, "volume": 100.0,
            "adj_open": adj, "adj_high": adj + 1, "adj_low": adj - 1, "adj_close": adj + 0.5, "adj_volume": 200.0,
            "div_cash": div, "split_factor": split}


ROWS = [_row("2026-08-31", 100, 50), _row("2026-09-01", 101, 50.5), _row("2026-09-02", 102, 51),
        _row("2026-09-03", 103, 51.5), _row("2026-09-04", 104, 52)]


def test_to_bars_is_ascending_dated_and_adjusted_by_default():
    b = mod.to_bars(list(reversed(ROWS)))                      # input order must not matter
    assert [x["time"] for x in b] == [r["date"] for r in ROWS]
    assert b[0] == {"time": "2026-08-31", "open": 50.0, "high": 51.0, "low": 49.0, "close": 50.5, "volume": 200.0}
    raw = mod.to_bars(ROWS, adjusted=False)
    assert raw[0]["open"] == 100.0 and raw[0]["volume"] == 100.0


def test_seam_idempotent_through_to_ohlcv():
    get_bars = mod.make_get_bars(read=lambda sym: ROWS if sym == "SPY" else [])
    once = get_bars("SPY", "D", count="5")
    assert to_ohlcv(once) == once
    assert to_ohlcv(to_ohlcv(once)) == once
    assert all(a["time"] < b["time"] for a, b in zip(once, once[1:]))


def test_count_trims_from_the_newest_end_and_accepts_str_or_int():
    get_bars = mod.make_get_bars(read=lambda sym: ROWS)
    assert [b["time"] for b in get_bars("SPY", "D", count="2")] == ["2026-09-03", "2026-09-04"]
    assert len(get_bars("SPY", "D", count=3)) == 3
    assert len(get_bars("SPY", "D", count="1200")) == 5                    # never pads


def test_unknown_symbol_and_bad_timespan():
    get_bars = mod.make_get_bars(read=lambda sym: [])
    with pytest.raises(KeyError):
        get_bars("NOPE", "D", count="5")
    with pytest.raises(ValueError):
        mod.make_get_bars(read=lambda sym: ROWS)("SPY", "M5", count="5")


def test_weekly_resample_labels_by_last_session_and_aggregates():
    # Mon 08-31 … Fri 09-04 is one ISO week; Tue 09-08 starts the next (Mon 09-07 is Labor Day)
    rows = ROWS + [_row("2026-09-08", 110, 55)]
    w = mod.weekly(mod.to_bars(rows))
    assert [x["time"] for x in w] == ["2026-09-04", "2026-09-08"]
    first = w[0]
    assert first["open"] == 50.0 and first["close"] == 52.5 and first["high"] == 53.0 and first["low"] == 49.0
    assert first["volume"] == 1000.0
    get_bars = mod.make_get_bars(read=lambda sym: rows)
    assert [x["time"] for x in get_bars("SPY", "W", count="1")] == ["2026-09-08"]
