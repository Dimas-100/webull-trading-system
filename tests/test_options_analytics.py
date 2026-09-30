import math
from datetime import date as D

import pytest

from webull_api import options_analytics as oa
from webull_api.options_chain import build_occ


# ── Task 1: primitives ──────────────────────────────────────────────────────────

def test_norm_cdf_reference():
    assert oa.norm_cdf(0) == pytest.approx(0.5, abs=1e-9)
    assert oa.norm_cdf(1.96) == pytest.approx(0.9750021, abs=1e-5)
    assert oa.norm_cdf(-1) == pytest.approx(1 - oa.norm_cdf(1), abs=1e-12)


def test_bs_price_reference():
    assert oa.bs_price(100, 100, 0.05, 0.20, 1.0, "C") == pytest.approx(10.4506, abs=1e-3)
    assert oa.bs_price(100, 100, 0.05, 0.20, 1.0, "P") == pytest.approx(5.5735, abs=1e-3)


def test_bs_d1_d2_and_delta_reference():
    d1, d2 = oa.d1_d2(100, 100, 0.05, 0.20, 1.0)
    assert d1 == pytest.approx(0.35, abs=1e-9)
    assert d2 == pytest.approx(0.15, abs=1e-9)
    assert oa.bs_delta(100, 100, 0.05, 0.20, 1.0, "C") == pytest.approx(0.6368, abs=1e-4)


def test_put_call_parity():
    S, K, r, sig, t = 123.0, 110.0, 0.03, 0.27, 0.5
    c = oa.bs_price(S, K, r, sig, t, "C")
    p = oa.bs_price(S, K, r, sig, t, "P")
    assert c - p == pytest.approx(S - K * math.exp(-r * t), abs=1e-9)


def test_year_fraction_floors_at_expiry():
    assert oa.year_fraction(D(2026, 1, 1), D(2026, 1, 1)) == pytest.approx(1e-6, abs=1e-9)
    assert oa.year_fraction(D(2026, 1, 1), D(2026, 12, 31)) == pytest.approx(364 / 365, abs=1e-6)


def test_normalize_iv_and_to_float():
    assert oa.normalize_iv("0.32") == pytest.approx(0.32)
    assert oa.normalize_iv("32") == pytest.approx(0.32)
    assert oa.normalize_iv("0") is None
    assert oa.normalize_iv(None) is None
    assert oa.to_float("8.70") == pytest.approx(8.70)
    assert oa.to_float("") is None and oa.to_float(None) is None


def test_normalize_iv_high_decimal_iv_not_crushed():
    # Webull delivers IV as a decimal: a true 350% IV arrives as 3.5 and must stay 3.5,
    # not be misread as percent and crushed to 3.5% (cutoff is 5, not 3).
    assert oa.normalize_iv(3.5) == pytest.approx(3.5)
    assert oa.normalize_iv("4.2") == pytest.approx(4.2)
    assert oa.normalize_iv("350") == pytest.approx(3.5)   # >5 is still read as percent


# ── Task 2: expected move ────────────────────────────────────────────────────────

def test_expected_move_iv():
    em = oa.expected_move(100.0, 0.20, 0.25)
    assert em["sigma1"] == pytest.approx(10.0, abs=1e-9)
    assert em["sigma1_pct"] == pytest.approx(0.10, abs=1e-9)
    assert em["upper"] == pytest.approx(110.0) and em["lower"] == pytest.approx(90.0)


def test_expected_move_straddle_crosscheck():
    em = oa.expected_move(100.0, 0.20, 0.25, atm_straddle=10 * math.sqrt(2 / math.pi))
    assert em["straddle_implied_sigma1"] == pytest.approx(10.0, abs=1e-6)


def test_expected_move_none_safe():
    assert oa.expected_move(100.0, None, 0.25)["sigma1"] is None


# ── Task 3: strategy analytics ───────────────────────────────────────────────────

def _leg(kind, side, strike, premium, qty=1, **g):
    return {"kind": kind, "side": side, "strike": strike, "premium": premium, "qty": qty, **g}


def test_long_call_single():
    legs = [_leg("C", "BUY", 100, 4.0, iv=0.2, delta=0.5)]
    a = oa.analyze_strategy(100, legs, 0.04, 0.5, 0.2)
    assert a["breakevens"] == pytest.approx([104.0])
    assert a["max_loss_dollar"] == pytest.approx(400.0)
    assert a["max_profit"] is None and a["unbounded_profit"] is True
    assert 0.0 < a["pop"] < 1.0
    assert a["net_greeks"]["delta"] == pytest.approx(0.5)


def test_bull_call_debit_spread():
    legs = [_leg("C", "BUY", 100, 4.0), _leg("C", "SELL", 105, 2.0)]
    a = oa.analyze_strategy(102, legs, 0.04, 0.5, 0.2)
    assert a["net_debit"] == pytest.approx(2.0)
    assert a["max_profit_dollar"] == pytest.approx(300.0)
    assert a["max_loss_dollar"] == pytest.approx(200.0)
    assert a["breakevens"] == pytest.approx([102.0])
    assert a["risk_reward"] == pytest.approx(1.5)


def test_bull_put_credit_spread():
    legs = [_leg("P", "SELL", 100, 3.0), _leg("P", "BUY", 95, 1.0)]
    a = oa.analyze_strategy(101, legs, 0.04, 0.5, 0.2)
    assert a["net_debit"] == pytest.approx(-2.0)
    assert a["max_profit_dollar"] == pytest.approx(200.0)
    assert a["max_loss_dollar"] == pytest.approx(300.0)
    assert a["breakevens"] == pytest.approx([98.0])
    assert a["pop"] > 0.5


def test_short_leg_greek_sign():
    a = oa.analyze_strategy(100, [_leg("C", "SELL", 100, 4.0, delta=0.5)], 0.04, 0.5, 0.2)
    assert a["net_greeks"]["delta"] == pytest.approx(-0.5)


def test_payoff_curve_endpoints_match_extremes():
    legs = [_leg("C", "BUY", 100, 4.0), _leg("C", "SELL", 105, 2.0)]
    pts = oa.payoff_curve(legs, 80, 130, n=51)
    pnls = [p["pnl"] for p in pts]
    assert max(pnls) == pytest.approx(3.0, abs=1e-6)
    assert min(pnls) == pytest.approx(-2.0, abs=1e-6)


def test_analyze_none_safe():
    a = oa.analyze_strategy(100, [_leg("C", "BUY", 100, None)], 0.04, 0.5, 0.2)
    assert a["max_loss_dollar"] is None


# ── Task 4: IV rank ──────────────────────────────────────────────────────────────

def test_historical_volatility_constant_returns_zero():
    assert oa.historical_volatility([100, 100, 100, 100], window=3) == pytest.approx(0.0, abs=1e-9)


def test_historical_volatility_known_series():
    hv = oa.historical_volatility([100, 101, 100, 102, 101], window=4)
    assert hv is not None and hv > 0


def test_iv_rank_and_percentile():
    series = [0.2, 0.3, 0.4, 0.5]
    assert oa.iv_rank(0.5, series) == pytest.approx(100.0)
    assert oa.iv_rank(0.2, series) == pytest.approx(0.0)
    assert oa.iv_percentile(0.4, series) == pytest.approx(75.0)


def test_iv_vs_hv_label():
    assert oa.iv_vs_hv(0.30, 0.20)["label"] == "elevated"
    assert oa.iv_vs_hv(0.10, 0.20)["label"] == "subdued"
    assert oa.iv_vs_hv(None, 0.20)["ratio"] is None


# ── Task 5: orchestrators (injected; no network) ─────────────────────────────────

def _snap(rows):
    table = {r["symbol"]: r for r in rows}

    def fn(csv):
        return [table[s] for s in csv.split(",") if s in table]

    return fn


def test_expected_move_for_uses_atm_iv():
    exp = D(2026, 7, 17)
    call = build_occ("AAPL", exp, "C", 100)
    put = build_occ("AAPL", exp, "P", 100)
    rows = [{"symbol": call, "imp_vol": "0.20", "bid": "5", "ask": "5"},
            {"symbol": put, "imp_vol": "0.20", "bid": "5", "ask": "5"}]
    em = oa.expected_move_for("AAPL", exp.isoformat(), today=D(2026, 4, 18),
                              spot_fn=lambda s: 100.0, snapshot_fn=_snap(rows),
                              bars_fn=lambda s, **k: [], opt_bars_fn=lambda s, **k: [])
    assert em["sigma1"] is not None
    assert em["iv_rank_method"] in {"history", "hv_proxy", "unavailable"}


def test_pop_long_call_unchanged():
    a = oa.analyze_strategy(100, [{"kind": "C", "side": "BUY", "strike": 100, "premium": 4.0, "qty": 1, "iv": 0.2}], 0.04, 0.5, 0.2)
    d2 = (math.log(100 / 104) + (0.04 - 0.5 * 0.2 ** 2) * 0.5) / (0.2 * math.sqrt(0.5))
    assert a["pop"] == pytest.approx(oa.norm_cdf(d2), abs=1e-3)


def test_pop_iron_condor_profit_between():
    legs = [{"kind": "P", "side": "SELL", "strike": 95, "premium": 1.5, "qty": 1, "iv": 0.25},
            {"kind": "P", "side": "BUY", "strike": 90, "premium": 0.7, "qty": 1, "iv": 0.25},
            {"kind": "C", "side": "SELL", "strike": 105, "premium": 1.5, "qty": 1, "iv": 0.25},
            {"kind": "C", "side": "BUY", "strike": 110, "premium": 0.7, "qty": 1, "iv": 0.25}]
    a = oa.analyze_strategy(100, legs, 0.04, 0.5, 0.25)
    assert len(a["breakevens"]) == 2
    b1, b2 = a["breakevens"]

    def p_above(x):
        d2 = (math.log(100 / x) + (0.04 - 0.5 * 0.25 ** 2) * 0.5) / (0.25 * math.sqrt(0.5))
        return oa.norm_cdf(d2)

    assert a["pop"] == pytest.approx(p_above(b1) - p_above(b2), abs=1e-3)
    assert 0 < a["pop"] < 1 and a["max_loss_dollar"] is not None


def test_pop_long_straddle_profit_outside():
    legs = [{"kind": "C", "side": "BUY", "strike": 100, "premium": 5.0, "qty": 1, "iv": 0.3},
            {"kind": "P", "side": "BUY", "strike": 100, "premium": 5.0, "qty": 1, "iv": 0.3}]
    a = oa.analyze_strategy(100, legs, 0.04, 0.5, 0.3)
    assert len(a["breakevens"]) == 2
    b1, b2 = a["breakevens"]

    def p_above(x):
        d2 = (math.log(100 / x) + (0.04 - 0.5 * 0.3 ** 2) * 0.5) / (0.3 * math.sqrt(0.5))
        return oa.norm_cdf(d2)

    assert a["pop"] == pytest.approx((1 - p_above(b1)) + p_above(b2), abs=1e-3)


def test_prob_of_touch_bounds_and_doubling():
    pot = oa.prob_of_touch(100, 110, 0.3, 0.5)
    n = oa.norm_cdf(-abs(math.log(110 / 100)) / (0.3 * math.sqrt(0.5)))
    assert pot == pytest.approx(min(1.0, 2 * n), abs=1e-4)   # PoT is rounded to 4dp
    assert 0 <= oa.prob_of_touch(100, 200, 0.2, 0.05) <= 1
    assert oa.prob_of_touch(100, 110, None, 0.5) is None


def test_expected_value_fair_option_near_zero():
    S, K, r, sig, t = 100, 100, 0.04, 0.25, 0.5
    prem = oa.bs_price(S, K, r, sig, t, "C")
    leg = {"kind": "C", "side": "BUY", "strike": K, "premium": prem, "qty": 1}
    ev = oa.expected_value(S, [leg], r, t, sig)
    assert ev == pytest.approx(prem * (math.exp(r * t) - 1), abs=0.06)


@pytest.mark.parametrize("sig,t", [(0.5, 0.5), (0.6, 1.0), (0.8, 1.0)])
def test_expected_value_fair_put_high_vol(sig, t):
    # Regression: fixed [0.4, 2.5]x-spot bounds truncated real probability mass at high
    # vol — a BS-fair long ATM put read EV -2.98 instead of +0.87 at sigma=0.6/t=1 (a
    # sign flip). Bounds now scale with sigma*sqrt(t), so EV == prem*(e^rt - 1) tightly.
    S = K = 100.0
    r = 0.04
    prem = oa.bs_price(S, K, r, sig, t, "P")
    leg = {"kind": "P", "side": "BUY", "strike": K, "premium": prem, "qty": 1}
    ev = oa.expected_value(S, [leg], r, t, sig)
    assert ev == pytest.approx(prem * (math.exp(r * t) - 1), abs=0.02)


def test_expected_value_short_leg_not_overstated_high_vol():
    # The truncation error's dangerous direction: short legs looked better than fair.
    S = K = 100.0
    r, sig, t = 0.04, 0.6, 1.0
    prem = oa.bs_price(S, K, r, sig, t, "P")
    leg = {"kind": "P", "side": "SELL", "strike": K, "premium": prem, "qty": 1}
    ev = oa.expected_value(S, [leg], r, t, sig)
    assert ev == pytest.approx(-prem * (math.exp(r * t) - 1), abs=0.02)


def test_scenario_grid_center_and_move():
    S, K, r, sig, t = 100, 100, 0.04, 0.25, 0.5
    prem = oa.bs_price(S, K, r, sig, t, "C")
    leg = {"kind": "C", "side": "BUY", "strike": K, "premium": prem, "qty": 1, "iv": sig}
    g = oa.scenario_grid(S, [leg], r, t, price_moves=(0.0, 0.05), iv_shifts=(0.0,))
    assert g["rows"][0]["pnl"][0] == pytest.approx(0.0, abs=0.01)
    expected_up = (oa.bs_price(105, K, r, sig, t, "C") - prem) * 100
    assert g["rows"][1]["pnl"][0] == pytest.approx(expected_up, abs=0.01)  # P&L rounded to cents


def test_analyze_strategy_includes_ev_and_pot():
    a = oa.analyze_strategy(100, [{"kind": "C", "side": "BUY", "strike": 100, "premium": 4.0, "qty": 1, "iv": 0.2}], 0.04, 0.5, 0.2)
    assert "expected_value" in a and "prob_of_touch" in a
    assert a["prob_of_touch"] is None or 0 <= a["prob_of_touch"] <= 1


def test_analyze_for_includes_scenario_and_liquidity():
    exp = D(2026, 7, 17)
    c100 = build_occ("AAPL", exp, "C", 100)
    rows = [{"symbol": c100, "bid": "3.9", "ask": "4.1", "imp_vol": "0.2", "delta": "0.55",
             "open_interest": "4000", "volume": "800"}]
    a = oa.analyze_for("AAPL", exp.isoformat(), [{"symbol": c100, "side": "BUY", "quantity": "1"}],
                       today=D(2026, 4, 18), spot_fn=lambda s: 100.0, snapshot_fn=_snap(rows))
    assert a["scenario"]["rows"] and "liquidity" in a
    assert a["liquidity"]["label"] in {"good", "fair", "poor", "unknown"}


def test_expected_move_for_snaps_atm_to_strike_grid():
    # spot 298 is not a listed strike (spacing 5 -> nearest grid strike is 300); the orchestrator
    # must snap to 300 and find that call/put pair, not synthesize an invalid 298 strike.
    exp = D(2026, 7, 17)
    call = build_occ("AAPL", exp, "C", 300)
    put = build_occ("AAPL", exp, "P", 300)
    rows = [{"symbol": call, "imp_vol": "0.25", "bid": "6", "ask": "6.4"},
            {"symbol": put, "imp_vol": "0.25", "bid": "5.6", "ask": "6.0"}]
    em = oa.expected_move_for("AAPL", exp.isoformat(), today=D(2026, 4, 18),
                              spot_fn=lambda s: 298.0, snapshot_fn=_snap(rows),
                              bars_fn=lambda s, **k: [])
    assert em["iv"] == pytest.approx(0.25)
    assert em["sigma1"] is not None and em["straddle_move"] is not None


def test_analyze_for_builds_legs_from_occ():
    exp = D(2026, 7, 17)
    c100 = build_occ("AAPL", exp, "C", 100)
    c105 = build_occ("AAPL", exp, "C", 105)
    rows = [{"symbol": c100, "bid": "3.8", "ask": "4.2", "imp_vol": "0.2", "delta": "0.55"},
            {"symbol": c105, "bid": "1.8", "ask": "2.2", "imp_vol": "0.2", "delta": "0.35"}]
    a = oa.analyze_for("AAPL", exp.isoformat(),
                       [{"symbol": c100, "side": "BUY", "quantity": "1"},
                        {"symbol": c105, "side": "SELL", "quantity": "1"}],
                       today=D(2026, 4, 18), spot_fn=lambda s: 102.0, snapshot_fn=_snap(rows))
    assert a["analytics"]["net_debit"] == pytest.approx(2.0)
    assert a["analytics"]["breakevens"] == pytest.approx([102.0])
    assert a["payoff_curve"] and isinstance(a["payoff_curve"], list)
