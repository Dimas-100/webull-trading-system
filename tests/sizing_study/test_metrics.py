"""The §5 metrics: marked drawdown (a mid-trade dip counts), Calmar, utilisation, months, correlation."""
from __future__ import annotations

from pytest import approx

from tests.sizing_study.conftest import D, cell, marks, sig, stream
from webull_api.sizing_study import book, metrics
from webull_api.sizing_study.loaders import IBS, RSI2


def test_marked_drawdown_sees_a_dip_inside_a_trade_that_settles_a_winner():
    """The lot falls 20% mid-trade and still exits +10%: the marked series must show the 20%, which is
    the whole point of marking daily instead of stamping equity at the exits."""
    c = cell(f=1.0, slots=1)
    sigs = stream(sig(RSI2, "AAA", D[0], D[3], 10.0))
    res = book.run(c, D[:4], sigs, marks({"AAA": [100.0, 90.0, 80.0, 999.0]}))
    assert res.equity == approx([100_000.0, 90_000.0, 80_000.0, 110_000.0])
    dd = metrics.max_drawdown(res.dates, res.equity, res.start_equity)
    assert round(dd["pct"], 6) == 20.0
    assert dd["peak_date"] is None and dd["peak_equity"] == 100_000.0    # the opening equity is the peak
    assert dd["trough_date"] == D[2] and dd["trough_equity"] == 80_000.0


def test_drawdown_peak_and_trough_dates_come_from_the_deepest_fall():
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    eq = [110_000.0, 99_000.0, 120_000.0, 60_000.0]
    dd = metrics.max_drawdown(dates, eq, 100_000.0)
    assert round(dd["pct"], 6) == 50.0 and dd["peak_date"] == "2020-01-06"
    assert dd["trough_date"] == "2020-01-07"


def test_calmar_is_cagr_over_the_marked_drawdown():
    assert metrics.calmar(10.0, 20.0) == 0.5
    assert metrics.calmar(-6.0, 30.0) == -0.2
    assert metrics.calmar(10.0, 0.0) is None and metrics.calmar(None, 20.0) is None


def test_cagr_compounds_the_marked_series_over_its_own_span():
    dates = ["2010-01-04", "2020-01-03"]           # 3,651 days ~ 9.996 years
    cg = metrics.cagr_pct(dates, [100_000.0, 200_000.0], 100_000.0)
    assert 7.1 < cg < 7.3


def test_yearly_and_monthly_returns_run_off_the_previous_period_close():
    dates = ["2020-01-31", "2020-02-28", "2021-01-29"]
    eq = [110_000.0, 121_000.0, 242_000.0]
    monthly = dict(metrics.monthly_returns(dates, eq, 100_000.0))
    assert round(monthly["2020-01"], 6) == 10.0 and round(monthly["2020-02"], 6) == 10.0
    yearly = dict(metrics.yearly_returns(dates, eq, 100_000.0))
    assert round(yearly["2020"], 6) == 21.0 and round(yearly["2021"], 6) == 100.0


def test_monthly_points_keep_only_the_last_session_of_each_month():
    dates = ["2020-01-02", "2020-01-31", "2020-02-03"]
    pts = metrics.monthly_points(dates, [1.0, 2.0, 3.0])
    assert pts == [{"date": "2020-01-31", "equity": 2.0}, {"date": "2020-02-03", "equity": 3.0}]


def test_mean_utilisation_averages_open_lot_sizes_over_equity():
    assert metrics.mean_utilisation_pct([50_000.0, 0.0], [100_000.0, 100_000.0]) == 25.0


def test_pearson_is_the_plain_correlation_of_two_aligned_series():
    assert round(metrics.pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 6) == 1.0
    assert round(metrics.pearson([1.0, 2.0, 3.0], [3.0, 2.0, 1.0]), 6) == -1.0
    assert metrics.pearson([1.0, 1.0], [1.0, 2.0]) is None


def test_stacked_summary_carries_the_two_books_monthly_correlation():
    c = cell(f=0.5, slots=2, books=(RSI2, IBS))
    sigs = stream(sig(RSI2, "AAA", D[0], D[4], 0.0), sig(IBS, "BBB", D[0], D[4], 0.0))
    res = book.run(c, D, sigs, marks({"AAA": [100.0, 110.0, 120.0, 130.0, 140.0],
                                      "BBB": [100.0, 90.0, 80.0, 70.0, 60.0]}))
    out = metrics.summarize(res)
    assert out["trades_taken"] == 2 and out["months"] == 1
    assert out["book_monthly_correlation"] is None          # one month cannot carry a correlation
    assert out["mean_utilisation_pct"] is not None and out["data_notes"]["missing_marks"] == 0


def test_single_book_summary_has_no_correlation_and_reports_the_worst_year():
    c = cell(f=1.0, slots=1)
    dates = ["2020-12-31", "2021-12-31"]
    res = book.run(c, dates, stream(), marks({"AAA": [1.0, 1.0]}, calendar=dates))
    out = metrics.summarize(res)
    assert out["book_monthly_correlation"] is None
    assert out["worst_year"] in ("2020", "2021") and out["trades_taken"] == 0
