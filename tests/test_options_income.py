import pytest

from webull_api import options_income as inc


def test_covered_call_yield_known():
    y = inc.covered_call_yield(100, 105, 3, 30 / 365)
    assert y["static_return"] == pytest.approx(0.03, abs=1e-9)
    assert y["if_called_return"] == pytest.approx(0.08, abs=1e-9)         # (3 + 5)/100
    assert y["static_annualized"] == pytest.approx(0.03 * 365 / 30, abs=1e-6)
    assert y["downside_breakeven"] == pytest.approx(97.0)
    assert y["cushion_pct"] == pytest.approx(0.03)


def test_csp_yield_known():
    y = inc.csp_yield(95, 2, 30 / 365)
    assert y["return_on_cash"] == pytest.approx(2 / 95, abs=1e-4)   # rounded to 4dp
    assert y["effective_buy"] == pytest.approx(93.0)
    assert y["breakeven"] == pytest.approx(93.0)
    assert y["annualized"] == pytest.approx((2 / 95) * 365 / 30, abs=1e-3)


def test_income_yields_sorts_and_skips():
    rows = [{"strike": 105, "call": {"bid": "2.9", "ask": "3.1"}, "put": {}},
            {"strike": 110, "call": {"bid": "1.0", "ask": "1.2"}, "put": {}},
            {"strike": 115, "call": {}, "put": {}}]  # no premium -> skipped
    out = inc.income_yields(rows, 100, 30 / 365, "call")
    assert [r["strike"] for r in out] == [105, 110]
    assert out[0]["static_annualized"] >= out[1]["static_annualized"]


def test_income_yields_filters_itm():
    # CSP wants OTM puts (strike <= spot); an ITM put (strike 110 > spot 100) is excluded.
    rows = [{"strike": 95, "call": {}, "put": {"bid": "1.9", "ask": "2.1"}},
            {"strike": 110, "call": {}, "put": {"bid": "11.0", "ask": "11.4"}}]
    out = inc.income_yields(rows, 100, 30 / 365, "put")
    assert [r["strike"] for r in out] == [95]
    # covered calls want OTM calls (strike >= spot); an ITM call (strike 90) is excluded.
    rows2 = [{"strike": 105, "call": {"bid": "2.0", "ask": "2.2"}, "put": {}},
             {"strike": 90, "call": {"bid": "12.0", "ask": "12.4"}, "put": {}}]
    out2 = inc.income_yields(rows2, 100, 30 / 365, "call")
    assert [r["strike"] for r in out2] == [105]


def test_sub_one_day_dte_never_annualizes():
    # Expiration-day t floors at 1e-6 (dte ~ 3e-4): the x365/dte annualization would
    # explode ~10^6x. The period return stays; annualized honestly degrades to None.
    y = inc.covered_call_yield(100, 105, 3, 1e-6)
    assert y["static_return"] == pytest.approx(0.03)
    assert y["static_annualized"] is None and y["if_called_annualized"] is None
    half_day = inc.csp_yield(95, 2, 0.5 / 365)
    assert half_day["return_on_cash"] == pytest.approx(2 / 95, abs=1e-4)
    assert half_day["annualized"] is None
    # a full 1-day dte still annualizes
    assert inc.csp_yield(95, 2, 1 / 365)["annualized"] == pytest.approx((2 / 95) * 365, abs=1e-2)


def test_income_yields_sorts_none_annualized_last():
    rows = [{"strike": 105, "call": {"bid": "2.9", "ask": "3.1"}, "put": {}},
            {"strike": 110, "call": {"bid": "1.0", "ask": "1.2"}, "put": {}}]
    out = inc.income_yields(rows, 100, 1e-6, "call")   # expiration day -> annualized None
    assert [r["static_annualized"] for r in out] == [None, None]
    assert [r["strike"] for r in out] == [105, 110]    # stable, no 10^8% figures on top


def test_none_safe():
    assert inc.covered_call_yield(None, 105, 3, 0.1) is None
    assert inc.csp_yield(0, 3, 0.1) is None
    assert inc.income_yields([], 100, 0.1, "put") == []


def test_income_for_injected():
    from datetime import date as D
    chain = {"rows": [{"strike": 95, "call": {}, "put": {"bid": "2.0", "ask": "2.2"}}]}
    r = inc.income_for("AAPL", "2026-07-17", "put", today=D(2026, 6, 17),
                       spot_fn=lambda s: 100.0, chain_fn=lambda *a, **k: chain)
    assert r["side"] == "put" and r["rows"][0]["strike"] == 95 and r["rows"][0]["breakeven"] < 95
