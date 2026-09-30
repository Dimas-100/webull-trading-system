"""Point-in-time band/ADV filter: no look-ahead (enters the band on D -> in on D+1), ADV boundary,
fewer than 20 prior rows -> out, yearly cache round-trip. Store rows are built inline."""
from datetime import date, timedelta

from webull_api.tiingo import universe as uni

BAND = uni.Band("plan-v1", 5.0, 30.0, 5_000_000)
POOL_BAND = uni.Band("pool", 5.0, 100.0, 5_000_000)


def _rows(closes, vol=6_000_000, start=date(2026, 7, 1)):
    from datetime import timedelta
    out, d = [], start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append({"date": d.isoformat(), "close": c, "volume": vol, "high": c, "low": c, "open": c})
        d += timedelta(days=1)
    return out


def test_enters_the_band_on_d_and_joins_the_universe_on_d_plus_1():
    rows = _rows([40.0] * 25 + [29.0] + [29.0] * 3)      # first in-band close is row index 25
    cal = [r["date"] for r in rows]
    days = uni.in_band_days(rows, cal, BAND)
    assert days == cal[26:]                               # never the day of the close itself
    assert uni.in_band(rows, date.fromisoformat(cal[25]), BAND) is False
    assert uni.in_band(rows, date.fromisoformat(cal[26]), BAND) is True


def test_adv_boundary_is_inclusive_and_short_history_is_out():
    at = _rows([20.0] * 21, vol=5_000_000)
    assert uni.in_band_days(at, [at[-1]["date"]], BAND) == [at[-1]["date"]]
    under = _rows([20.0] * 21, vol=4_999_999)
    assert uni.in_band_days(under, [under[-1]["date"]], BAND) == []
    short = _rows([20.0] * 19)
    assert uni.in_band(short + [], date(2026, 9, 1), BAND) is False


def test_calendar_days_without_a_row_use_the_last_prior_row():
    rows = _rows([20.0] * 22)
    gap_day = "2026-09-30"                                # no row that day
    assert uni.in_band_days(rows, [gap_day], BAND) == [gap_day]


def test_cache_round_trip_and_month_union(tmp_path, monkeypatch):
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path))
    a = _rows([20.0] * 30)
    b = _rows([50.0] * 30)
    cal = [r["date"] for r in a]
    by = {"A": a, "B": b}
    got = uni.build_cache(cal[0], cal[-1], ["A", "B"], BAND, read=lambda s: by[s], calendar=cal)
    assert got[cal[-1]] == ["A"]
    assert uni.load(date.fromisoformat(cal[-1]), "plan-v1") == ["A"]
    assert uni.load(date.fromisoformat(cal[0]), "plan-v1") == []       # not enough history yet
    ym = cal[-1][:7]
    assert uni.load_month(ym, "plan-v1") == {"A"}
    assert (tmp_path / "universe" / "plan-v1" / f"{cal[-1][:4]}.csv").exists()


def test_year_rows_memo_is_invalidated_by_a_rebuild_and_keyed_by_the_store_dir(tmp_path, monkeypatch):
    # B5: `load` is called once per session day of a five-year window, so the year file is
    # memoized -- but a rebuild must be visible, and a different TIINGO_DIR must never be served
    # another store's rows.
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path / "one"))
    rows = _rows([20.0] * 30)
    cal = [r["date"] for r in rows]
    last = date.fromisoformat(cal[-1])
    uni.build_cache(cal[0], cal[-1], ["A"], BAND, read=lambda s: rows, calendar=cal)
    assert uni.load(last, "plan-v1") == ["A"]
    uni.build_cache(cal[0], cal[-1], ["Z"], BAND, read=lambda s: rows, calendar=cal)
    assert uni.load(last, "plan-v1") == ["Z"]                  # rebuild is visible
    monkeypatch.setenv("TIINGO_DIR", str(tmp_path / "two"))
    assert uni.load(last, "plan-v1") == []                     # a different store, not "Z"


def test_calendar_from_spy_rows_is_clipped_to_the_window():
    spy = _rows([400.0] * 10)
    cal = uni.calendar(spy, spy[2]["date"], spy[5]["date"])
    assert cal == [r["date"] for r in spy[2:6]]


def _flat(n, close=20.0, volume=6_000_000, start=date(2026, 8, 1)):
    return [{"date": (start + timedelta(days=i)).isoformat(), "close": close, "volume": volume,
             "high": close, "low": close, "open": close} for i in range(n)]


def test_latest_in_band_uses_only_the_last_row_and_trailing_adv_window():
    assert uni.latest_in_band(_flat(21), POOL_BAND) is True
    assert uni.latest_in_band(_flat(21, volume=4_999_999), POOL_BAND) is False   # under ADV
    assert uni.latest_in_band(_flat(21, close=500.0), POOL_BAND) is False        # over price band


def test_latest_in_band_boundaries_are_inclusive():
    assert uni.latest_in_band(_flat(20, close=5.0, volume=5_000_000), POOL_BAND) is True    # price min, ADV min
    assert uni.latest_in_band(_flat(20, close=100.0, volume=5_000_000), POOL_BAND) is True  # price max
    assert uni.latest_in_band(_flat(20, close=100.01, volume=5_000_000), POOL_BAND) is False
    assert uni.latest_in_band(_flat(20, close=4.99, volume=5_000_000), POOL_BAND) is False


def test_latest_in_band_fewer_than_adv_window_rows_is_false():
    assert uni.latest_in_band(_flat(19), POOL_BAND) is False
    assert uni.latest_in_band([], POOL_BAND) is False


def test_latest_in_band_rejects_a_stale_last_row():
    rows = _flat(21, start=date(2026, 8, 1))       # last row's date is 2026-08-21
    assert rows[-1]["date"] == "2026-08-21"
    assert uni.latest_in_band(rows, POOL_BAND, not_before="2026-08-21") is True
    assert uni.latest_in_band(rows, POOL_BAND, not_before="2026-08-22") is False


def test_pool_returns_sorted_names_passing_latest_in_band():
    by = {"ZZZ": _flat(21), "AAA": _flat(21), "OUT": _flat(21, close=500.0), "SHORT": _flat(10)}
    assert uni.pool(list(by), POOL_BAND, read=lambda s: by[s]) == ["AAA", "ZZZ"]


def test_pool_honours_not_before():
    by = {"FRESH": _flat(21, start=date(2026, 9, 1)), "STALE": _flat(21, start=date(2026, 8, 1))}
    assert uni.pool(list(by), POOL_BAND, read=lambda s: by[s], not_before="2026-09-01") == ["FRESH"]
