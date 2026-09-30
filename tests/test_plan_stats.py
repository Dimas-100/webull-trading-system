import math
import statistics

import pytest

from webull_web import plan_stats as ps


def test_describe_matches_the_statistics_module_on_a_known_sample():
    r = [1.0, 2.0, 3.0, 4.0]
    d = ps.describe(r, pnl_usd=[10.0, 20.0, -5.0, 15.0], holds=[1, 2, 3, 4], growth_index=[100, 110, 99, 120])
    assert d["n"] == 4
    assert d["mean_pct"] == pytest.approx(statistics.mean(r))
    assert d["variance"] == pytest.approx(statistics.variance(r))          # n-1
    assert d["sd_pct"] == pytest.approx(statistics.stdev(r))
    assert d["se_pct"] == pytest.approx(statistics.stdev(r) / math.sqrt(4))
    assert d["win_rate"] == 1.0
    assert d["expectancy_usd"] == pytest.approx(10.0)
    assert d["best_pct"] == 4.0 and d["worst_pct"] == 1.0
    assert d["mean_hold"] == pytest.approx(2.5)
    assert d["max_drawdown_pct"] == pytest.approx(10.0)                    # 110 -> 99
    assert d["return_per_risk"] == pytest.approx(2.5 / statistics.stdev(r))


def test_describe_win_rate_counts_only_positive_returns():
    d = ps.describe([-1.0, 0.0, 2.0])
    assert d["win_rate"] == pytest.approx(1 / 3)


def test_describe_small_samples_and_empty():
    assert ps.describe([]) is None
    one = ps.describe([1.5])
    assert one["n"] == 1 and one["mean_pct"] == 1.5
    assert one["variance"] is None and one["sd_pct"] is None and one["se_pct"] is None
    assert one["return_per_risk"] is None
    flat = ps.describe([2.0, 2.0])                      # sd == 0 -> no ratio
    assert flat["sd_pct"] == 0.0 and flat["return_per_risk"] is None


def test_max_drawdown_is_peak_to_trough_in_percent():
    assert ps.max_drawdown_pct([100, 120, 90, 130, 117]) == pytest.approx(25.0)   # 120 -> 90
    assert ps.max_drawdown_pct([100, 101, 102]) == 0.0
    assert ps.max_drawdown_pct([]) is None


def test_histogram_bins_are_fixed_and_tails_are_collected():
    bins = ps.histogram([-37.8, -10.0, -9.99, 0.0, 0.49, 0.5, 9.99, 10.0, 22.3])
    assert len(bins) == 42
    assert bins[0]["lo"] is None and bins[0]["hi"] == -10.0 and bins[0]["center"] == -10.25
    assert bins[-1]["lo"] == 10.0 and bins[-1]["hi"] is None and bins[-1]["center"] == 10.25
    assert bins[0]["count"] == 1 and bins[0]["trades"] == [0]                 # -37.8 -> low tail
    assert bins[1]["lo"] == -10.0 and bins[1]["count"] == 2                      # -10.0 and -9.99
    zero = next(b for b in bins if b["lo"] == 0.0)
    assert zero["hi"] == 0.5 and zero["count"] == 2 and zero["trades"] == [3, 4]  # 0.0, 0.49
    half = next(b for b in bins if b["lo"] == 0.5)
    assert half["count"] == 1
    top = next(b for b in bins if b["lo"] == 9.5)
    assert top["count"] == 1                                                     # 9.99
    assert bins[-1]["count"] == 2 and bins[-1]["trades"] == [7, 8]              # 10.0, 22.3
    assert sum(b["count"] for b in bins) == 9


def test_normal_curve_area_equals_the_sample_count():
    centers = [b["center"] for b in ps.histogram([])]
    ys = ps.normal_curve(centers, mean=0.8, sd=2.0, n=200)
    assert 190 <= sum(ys) <= 200.5                    # ~all mass inside +-10 for sd 2
    assert ps.normal_curve(centers, mean=0.0, sd=None, n=5) == [0.0] * 42
    assert ps.normal_curve(centers, mean=0.0, sd=0.0, n=5) == [0.0] * 42


def test_distribution_carries_both_curves_scaled_to_the_actual_n():
    r = [0.5, 1.0, -0.5, 2.0, 0.0]
    d = ps.distribution(r, expected_mean=0.84, expected_sd=3.9, expected_n=3221)
    assert d["n"] == 5 and d["mean"] == pytest.approx(statistics.mean(r))
    assert len(d["bins"]) == 42 and len(d["fitted"]) == 42 and len(d["expected"]) == 42
    assert 4.0 <= sum(d["fitted"]) <= 5.05
    assert 4.5 <= sum(d["expected"]) <= 5.05             # scaled to n=5, not 3221
    assert d["expected_mean"] == 0.84 and d["expected_sd"] == 3.9 and d["expected_n"] == 3221
    none = ps.distribution(r)
    assert none["expected"] is None and none["expected_mean"] is None


def test_describe_reports_average_win_and_loss_and_the_expectancy_identity():
    s = ps.describe([2.0, -1.0, 3.0, -2.0, 4.0])
    assert s["avg_win_pct"] == 3.0 and s["avg_loss_pct"] == 1.5
    p = s["win_rate"]
    assert p * s["avg_win_pct"] - (1 - p) * s["avg_loss_pct"] == pytest.approx(s["mean_pct"])   # E = p·W − (1−p)·L


def test_describe_band95_and_trades_to_resolve():
    s = ps.describe([2.0, -1.0, 3.0, -2.0, 4.0])          # mean 1.2, sd 2.588436, se 1.157584
    assert s["band95_lo_pct"] == pytest.approx(-1.0689, abs=1e-3)
    assert s["band95_hi_pct"] == pytest.approx(3.4689, abs=1e-3)
    assert s["trades_to_resolve"] == 18                    # ceil((1.96 * 2.588436 / 1.2) ** 2) = ceil(17.87)
    assert ps.describe([-1.0, -2.0])["trades_to_resolve"] is None       # a non-positive mean never resolves
    one = ps.describe([1.5])
    assert one["avg_win_pct"] == 1.5 and one["avg_loss_pct"] is None
    assert one["band95_lo_pct"] is None and one["trades_to_resolve"] is None


def test_convergence_walks_the_cumulative_mean_with_its_standard_error():
    rows = ps.convergence([2.0, -1.0, 3.0])
    assert rows[0] == {"n": 1, "cum_mean_pct": 2.0, "se_pct": None}
    assert rows[1]["n"] == 2 and rows[1]["cum_mean_pct"] == 0.5 and rows[1]["se_pct"] == pytest.approx(1.5)
    assert rows[2]["cum_mean_pct"] == pytest.approx(1.333333) and rows[2]["se_pct"] == pytest.approx(1.201850, abs=1e-5)
    assert ps.convergence([]) == []


def test_excursions_are_the_best_and_worst_close_in_percent_of_entry():
    assert ps.excursions(100.0, [101.0, 104.0, 99.0, 102.0]) == (4.0, -1.0)
    assert ps.excursions(100.0, []) == (None, None)
    assert ps.excursions(0.0, [1.0]) == (None, None)


def test_by_year_from_curve_measures_each_calendar_year_end_to_end():
    pts = [{"date": "2020-01-02", "equity": 100.0}, {"date": "2020-06-30", "equity": 130.0}, {"date": "2020-12-31", "equity": 110.0},
           {"date": "2021-12-31", "equity": 99.0}]
    assert ps.by_year_from_curve(pts) == [{"year": 2020, "return_pct": 10.0}, {"year": 2021, "return_pct": -10.0}]
    assert ps.by_year_from_curve([{"date": "2020-03-01", "value": 50.0}, {"date": "2020-03-02", "value": 55.0}]) == [{"year": 2020, "return_pct": 10.0}]
    assert ps.by_year_from_curve([]) == []


def test_hold_histogram_buckets_whole_days_and_an_open_tail():
    h = ps.hold_histogram([1.0, 1.4, 2.0, 5.0, 12.0, 0.6])
    assert [b["label"] for b in h] == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11+"]
    assert {b["label"]: b["count"] for b in h if b["count"]} == {"1": 3, "2": 1, "5": 1, "11+": 1}


def test_describe_reports_the_median_and_the_two_win_loss_ratios():
    s = ps.describe([2.0, -1.0, 3.0, -2.0, 4.0])
    assert s["median_pct"] == pytest.approx(2.0)
    assert s["profit_factor"] == pytest.approx(3.0)            # 9 won / 3 lost
    assert s["payoff_ratio"] == pytest.approx(2.0)             # avg win 3.0 / avg loss 1.5
    # No loss at all is unmeasured, not infinite; losses with no win is a measured zero.
    assert ps.describe([1.0, 2.0])["profit_factor"] is None
    assert ps.describe([-1.0, -2.0])["profit_factor"] == 0.0


def test_describe_reports_the_shape_of_the_distribution():
    sym = ps.describe([1.0, 2.0, 3.0, 4.0, 5.0])
    assert sym["skewness"] == pytest.approx(0.0, abs=1e-9)      # symmetric
    assert sym["excess_kurtosis"] == pytest.approx(-1.2, abs=1e-6)   # flat-topped, computed by hand
    assert ps.describe([1.0, 1.0, 1.0, 10.0])["skewness"] > 0       # one long right tail
    assert ps.describe([1.0, 2.0, 3.0])["excess_kurtosis"] is None   # kurtosis needs n >= 4
    assert ps.describe([1.0, 2.0])["skewness"] is None              # skewness needs n >= 3
    assert ps.describe([2.0, 2.0, 2.0])["skewness"] is None         # no spread to measure against


def test_describe_counts_the_streaks_in_the_order_the_trades_closed():
    s = ps.describe([1.0, 2.0, -1.0, 3.0, 4.0, 5.0, -2.0])
    assert s["longest_win"] == 3 and s["longest_loss"] == 1
    assert s["current_streak"] == {"kind": "loss", "len": 1}
    # A zero return breaks a run and counts for neither side.
    z = ps.describe([1.0, 0.0, 1.0])
    assert z["longest_win"] == 1 and z["current_streak"] == {"kind": "win", "len": 1}
    assert ps.describe([1.0, 2.0, 0.0])["current_streak"] == {"kind": None, "len": 0}


def test_describe_new_fields_on_a_single_trade_and_on_nothing():
    one = ps.describe([1.5])
    assert one["median_pct"] == 1.5
    assert one["profit_factor"] is None and one["payoff_ratio"] is None
    assert one["skewness"] is None and one["excess_kurtosis"] is None
    assert one["longest_win"] == 1 and one["longest_loss"] == 0
    assert one["current_streak"] == {"kind": "win", "len": 1}
    assert ps.describe([]) is None


def test_time_stats_annualises_a_dated_growth_index():
    year = ps.time_stats([{"date": "2021-01-01", "value": 100.0}, {"date": "2022-01-01", "value": 110.0}])
    assert year["span_days"] == 365 and year["sessions"] == 2
    assert year["cagr_pct"] == pytest.approx(10.0, abs=1e-6)
    assert year["ann_vol_pct"] is None and year["sharpe"] is None     # one log return is not a spread
    assert year["max_dd_pct"] == 0.0 and year["calmar"] is None       # no drawdown to divide by
    logs = [math.log(1.1), math.log(0.9)]
    three = ps.time_stats([{"date": "2021-01-04", "value": 100.0}, {"date": "2021-01-05", "value": 110.0},
                           {"date": "2021-01-06", "value": 99.0}])
    assert three["ann_vol_pct"] == pytest.approx(statistics.stdev(logs) * math.sqrt(252) * 100.0, abs=1e-6)
    assert three["sharpe"] == pytest.approx(statistics.mean(logs) / statistics.stdev(logs) * math.sqrt(252), abs=1e-6)
    assert ps.time_stats([]) is None


def test_time_stats_measures_the_drawdown_depth_length_and_where_it_stands():
    recovered = ps.time_stats([{"date": "2021-01-01", "value": 100.0}, {"date": "2021-01-02", "value": 90.0},
                               {"date": "2021-01-03", "value": 95.0}, {"date": "2021-01-04", "value": 101.0}])
    assert recovered["max_dd_pct"] == pytest.approx(10.0)
    assert recovered["max_dd_days"] == 3                  # 01-01's peak regained on 01-04
    assert recovered["current_dd_pct"] == 0.0             # at a new high
    assert recovered["calmar"] == pytest.approx(recovered["cagr_pct"] / 10.0)
    open_dd = ps.time_stats([{"date": "2021-01-01", "value": 100.0}, {"date": "2021-01-02", "value": 90.0}])
    assert open_dd["max_dd_days"] == 1                    # unrecovered: counted to the last date
    assert open_dd["current_dd_pct"] == pytest.approx(10.0)


def test_underwater_is_the_fall_from_the_running_peak():
    pts = [{"date": "2021-01-01", "value": 100.0}, {"date": "2021-01-02", "value": 90.0},
           {"date": "2021-01-03", "value": 95.0}, {"date": "2021-01-04", "value": 101.0}]
    rows = ps.underwater(pts)
    assert [r["dd_pct"] for r in rows] == [0.0, 10.0, 5.0, 0.0]
    assert [r["date"] for r in rows] == ["2021-01-01", "2021-01-02", "2021-01-03", "2021-01-04"]
    assert ps.underwater([]) == []


def test_utilisation_counts_open_lots_and_the_capital_at_work():
    trades = [{"symbol": "A", "entry_date": "2021-01-01", "exit_date": "2021-01-03", "shares": 10, "entry_price": 10.0},
              {"symbol": "B", "entry_date": "2021-01-02", "exit_date": "2021-01-04", "shares": 5, "entry_price": 20.0}]
    eq = [{"date": f"2021-01-0{d}", "value": 1000.0} for d in (1, 2, 3, 4)]
    u = ps.utilisation(trades, eq)
    assert u["sessions"] == 4
    assert u["avg_open_lots"] == pytest.approx(1.0)                  # lots per day: 1, 2, 1, 0
    assert u["avg_capital_at_work_pct"] == pytest.approx(10.0)       # $100, $200, $100, $0 of $1,000
    # A trade with no shares is still a lot, but adds no capital the percent can claim.
    sizeless = ps.utilisation([{"entry_date": "2021-01-01", "exit_date": "2021-01-04"}], eq)
    assert sizeless["avg_open_lots"] == pytest.approx(0.75) and sizeless["avg_capital_at_work_pct"] == 0.0
    assert ps.utilisation(trades, []) is None


def test_by_symbol_tallies_each_name():
    t = [{"symbol": "AMZN", "return_pct": 2.0}, {"symbol": "AAPL", "return_pct": -1.0}, {"symbol": "AMZN", "return_pct": -3.0}]
    assert ps.by_symbol(t) == [
        {"symbol": "AAPL", "n": 1, "win_rate": 0.0, "mean_pct": -1.0, "best_pct": -1.0, "worst_pct": -1.0},
        {"symbol": "AMZN", "n": 2, "win_rate": 0.5, "mean_pct": -0.5, "best_pct": 2.0, "worst_pct": -3.0},
    ]
