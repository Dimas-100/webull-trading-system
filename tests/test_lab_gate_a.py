import pytest
from webull_api.lab.gate_a import (NotEnoughData, make_folds, stress_cost,
                                    fold_metrics, pooled_expectancy, regime_coverage_ok,
                                    _warmup_bars, expectancy_ci, dsr, deflated_edge_floor,
                                    breadth, param_neighbors, screen_one)
from webull_api.lab.schema import (CostConfig, DEFAULT_GATE_A_CONFIG, Fold, GateAConfig,
                                    RegimeTag, WalkForwardReport, WindowResult)
from webull_api.strategy.cost import NO_COST
from webull_api.strategy.schema import EquityPoint, Strategy


def test_stress_cost_doubles_slippage_with_floor():
    c = stress_cost(CostConfig(slippage_pct=0.02))     # 2*0.02 = 0.04 < 0.10 floor
    assert c.slippage_pct == pytest.approx(0.10)
    c2 = stress_cost(CostConfig(slippage_pct=0.08))    # 2*0.08 = 0.16 > floor
    assert c2.slippage_pct == pytest.approx(0.16)


def test_stress_cost_carries_commission_and_liquidity():
    cfg = CostConfig(commission_flat=1.0, commission_per_share=0.005,
                     min_commission=1.0, liquidity_adv_frac=0.02, stop_slippage_pct=0.10)
    c = stress_cost(cfg)
    assert c.commission_flat == 1.0 and c.commission_per_share == 0.005
    assert c.min_commission == 1.0 and c.liquidity_adv_frac == 0.02
    assert c.stop_slippage_pct == 0.10


def test_make_folds_partitions_warmup_to_n_no_gaps_or_overlap():
    folds = make_folds(300, k=6, warmup=50, min_fold_bars=40, embargo=10)
    assert len(folds) == 6
    assert folds[0].start == 50                 # attribution starts after warmup
    assert folds[-1].end == 300                 # last fold absorbs the remainder
    for a, b in zip(folds, folds[1:]):
        assert a.end == b.start                 # contiguous, no gap, no overlap
        # no-look-ahead splitter: out_sample.lo > in_sample.hi (hi = last index of prior fold)
        assert b.start > a.end - 1
    for f in folds:
        assert f.end - f.start >= 40            # each test segment holds >= min_fold_bars


def test_make_folds_auto_reduces_k():
    # only room for 4 folds of >=40 over [20, 200) (180 usable)
    folds = make_folds(200, k=6, warmup=20, min_fold_bars=40, embargo=10)
    assert len(folds) == 4
    assert folds[0].start == 20 and folds[-1].end == 200


def test_make_folds_raises_when_under_three_folds_formable():
    with pytest.raises(NotEnoughData):
        make_folds(110, k=6, warmup=20, min_fold_bars=40, embargo=10)  # 90 usable -> max 2 folds


def _bar(t, o, h, l, c):
    return {"time": t, "open": o, "high": h, "low": l, "close": c, "volume": 1_000_000}


def _flat(n):
    return [_bar(f"t{i}", 50.0, 50.0, 50.0, 50.0) for i in range(n)]


def _chop(n):
    # seed-driven random ±2 around 50 so the SMA crossover has no systematic edge
    # (the old deterministic alternating pattern was mechanically exploitable: buy at 48, sell at 52)
    import random
    rng = random.Random(42)
    out = []
    for i in range(n):
        p = 50.0 + rng.choice([-2.0, 2.0])
        out.append(_bar(f"t{i}", p, p * 1.01, p * 0.99, p))
    return out


def _strat():
    return Strategy(name="x", symbol="X",
                    entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})


def _trend_bars(n):
    # a couple of crossings inside a generally rising series
    out = []
    price = 100.0
    for i in range(n):
        price *= 1.01 if (i // 4) % 2 == 0 else 0.997
        out.append(_bar(f"t{i}", price, price, price, price))
    return out


def test_warmup_bars_uses_max_period_plus_buffer():
    # slow SMA period 3 + default warmup_buffer 10
    assert _warmup_bars(_strat()) == 13


def test_warmup_covers_new_leaf_periods():
    # each of the 4 newer leaf types must feed its period into warmup sizing (previously fell
    # through _leaf_periods' type checks and contributed 0, so a big period like zscore(50)
    # would have run with a too-short warmup). Pin _warmup_bars AND trial.warmup_for (a thin
    # wrapper over the same _leaf_periods SSOT) so both agree on the shared contract:
    # max leaf period + GateAConfig.warmup_buffer.
    from webull_api.lab.trial import warmup_for
    buf = DEFAULT_GATE_A_CONFIG.warmup_buffer

    zs = Strategy(name="z", symbol="SPY",
                 entry={"type": "zscore", "period": 50, "threshold": -2.0, "comparison": "below"})
    assert _warmup_bars(zs) == 50 + buf
    assert warmup_for(zs) == 50 + buf

    dh = Strategy(name="d", symbol="SPY",
                 entry={"type": "drop_from_high", "lookback": 100, "pct": 8.0})
    assert _warmup_bars(dh) == 100 + buf
    assert warmup_for(dh) == 100 + buf

    cd = Strategy(name="c", symbol="SPY", entry={"type": "consec_down", "count": 4})
    assert _warmup_bars(cd) == 4 + buf
    assert warmup_for(cd) == 4 + buf

    ib = Strategy(name="i", symbol="SPY", entry={"type": "ibs", "level": 0.2, "side": "below"})
    assert _warmup_bars(ib) == 1 + buf
    assert warmup_for(ib) == 1 + buf


def test_fold_metrics_is_net_of_cost_and_carries_regime():
    bars = _trend_bars(120)
    fold = Fold(index=0, start=40, end=100)
    wr = fold_metrics(_strat(), bars, fold, warmup=13, cost=NO_COST)
    assert isinstance(wr, WindowResult)
    assert wr.fold_index == 0 and wr.start_index == 40 and wr.end_index == 100
    assert isinstance(wr.regime, RegimeTag)
    assert wr.ulcer_index >= 0.0


def test_fold_metrics_truncation_after_end_is_byte_identical():
    # no-look-ahead proof: bars beyond fold.end can never change the report
    bars = _trend_bars(160)
    fold = Fold(index=1, start=60, end=110)
    full = fold_metrics(_strat(), bars, fold, warmup=13, cost=NO_COST)
    trunc = fold_metrics(_strat(), bars[:fold.end], fold, warmup=13, cost=NO_COST)
    assert full.model_dump() == trunc.model_dump()


def test_pooled_expectancy_is_trade_weighted():
    a = WindowResult(fold_index=0, start_index=0, end_index=1, regime=RegimeTag(trend="up", vol="low"),
                     num_trades=10, expectancy=2.0, avg_trade_return_pct=0, profit_factor=1,
                     win_rate=0, return_pct=0, max_drawdown_pct=0, ulcer_index=0,
                     conditional_drawdown_95=0, sharpe=0, sortino=0)
    b = a.model_copy(update={"num_trades": 30, "expectancy": -1.0})
    # (10*2 + 30*-1) / 40 = -0.25
    assert pooled_expectancy([a, b]) == pytest.approx(-0.25)


def test_regime_coverage_requires_two_buckets_incl_high_vol():
    up_low = WindowResult(fold_index=0, start_index=0, end_index=1,
                          regime=RegimeTag(trend="up", vol="low"), num_trades=1, expectancy=1,
                          avg_trade_return_pct=0, profit_factor=1, win_rate=0, return_pct=0,
                          max_drawdown_pct=0, ulcer_index=0, conditional_drawdown_95=0,
                          sharpe=0, sortino=0)
    up_high = up_low.model_copy(update={"regime": RegimeTag(trend="up", vol="high")})
    assert not regime_coverage_ok([up_low], min_buckets=2, require_high_vol=True)        # one bucket
    assert not regime_coverage_ok([up_low, up_low], min_buckets=2, require_high_vol=True)  # still one
    assert regime_coverage_ok([up_low, up_high], min_buckets=2, require_high_vol=True)    # two + high


def test_expectancy_ci_is_deterministic_and_brackets_the_mean():
    data = [1.0, -0.5, 2.0, 0.5, -1.0, 1.5, 0.0, 0.8]
    lo1, hi1 = expectancy_ci(data, seed=0)
    lo2, hi2 = expectancy_ci(data, seed=0)
    assert (lo1, hi1) == (lo2, hi2)          # seeded -> reproducible
    assert lo1 <= sum(data) / len(data) <= hi1


def test_expectancy_ci_lower_bound_flips_at_boundary():
    # a clearly-positive sample -> LB > 0; shift it negative -> LB < 0
    pos = [3.0, 2.5, 3.5, 2.8, 3.2, 2.9, 3.1, 3.3, 2.7, 3.0]
    neg = [v - 6.0 for v in pos]
    assert expectancy_ci(pos, seed=0)[0] > 0
    assert expectancy_ci(neg, seed=0)[0] < 0


def test_expectancy_ci_empty():
    assert expectancy_ci([]) == (0.0, 0.0)


def _pos_returns(n, mu=0.001):
    # small positive-mean series with deterministic jitter
    import math
    return [mu + 0.01 * math.sin(i) for i in range(n)]


def test_dsr_monotone_decreasing_in_m():
    r = _pos_returns(300)
    assert dsr(r, m=1) > dsr(r, m=10) > dsr(r, m=1000)


def test_dsr_monotone_increasing_in_n():
    short, long = _pos_returns(60), _pos_returns(600)
    assert dsr(long, m=50) > dsr(short, m=50)


def test_dsr_near_zero_edge_passes_at_m1_fails_at_large_m():
    r = _pos_returns(800, mu=0.0008)
    assert dsr(r, m=1) > 0.95
    assert dsr(r, m=100000) < 0.95


def test_dsr_accepts_equity_curve():
    curve = [EquityPoint(time=f"t{i}", equity=100.0 + i) for i in range(50)]
    v = dsr(curve, m=5)
    assert 0.0 <= v <= 1.0


def test_deflated_edge_floor_monotone():
    assert deflated_edge_floor(0.0, m=1000, n=100, k_defl=0.5) > \
           deflated_edge_floor(0.0, m=2, n=100, k_defl=0.5)        # rises with m
    assert deflated_edge_floor(0.0, m=100, n=50, k_defl=0.5) > \
           deflated_edge_floor(0.0, m=100, n=5000, k_defl=0.5)     # falls with n


def test_breadth_fraction_and_passing_symbols():
    frac, passing = breadth({"AAPL": 0.5, "MSFT": -0.1, "SPY": 0.2, "XOM": -2.0}, min_frac=0.5)
    assert frac == pytest.approx(0.5)            # 2 of 4 positive
    assert passing == ["AAPL", "SPY"]            # sorted, positive only


def test_breadth_rejects_single_symbol_edge():
    frac, passing = breadth({"AAPL": 5.0, "MSFT": -0.2, "SPY": -0.3, "JPM": -0.1}, min_frac=0.5)
    assert frac < 0.5 and passing == ["AAPL"]    # below breadth_min_frac -> caller rejects


def test_breadth_empty():
    assert breadth({}, min_frac=0.5) == (0.0, [])


def test_param_neighbors_perturbs_integer_params_and_validates():
    s = Strategy(name="x", symbol="X",
                 entry={"type": "sma_cross", "fast": 20, "slow": 50, "direction": "above"})
    ns = param_neighbors(s)
    fasts = {n.entry.fast for n in ns}
    slows = {n.entry.slow for n in ns}
    assert 19 in fasts and 21 in fasts          # fast +/- 1
    assert 49 in slows and 51 in slows          # slow +/- 1
    # fast must stay < slow: a fast=20 -> 21 is fine, but no neighbor violates the validator
    assert all(n.entry.fast < n.entry.slow for n in ns)


def test_param_neighbors_handles_composite_leaves():
    s = Strategy(name="x", symbol="X",
                 entry={"type": "all_of", "conditions": [
                     {"type": "rsi", "period": 14, "threshold": 30, "comparison": "below"},
                     {"type": "breakout", "lookback": 20, "direction": "high"}]})
    ns = param_neighbors(s)
    periods = {c.period for n in ns for c in n.entry.conditions if c.type == "rsi"}
    lookbacks = {c.lookback for n in ns for c in n.entry.conditions if c.type == "breakout"}
    assert 13 in periods and 15 in periods
    assert 19 in lookbacks and 21 in lookbacks


def _rising(n, step=1.005, start=50.0):
    out, p = [], start
    for i in range(n):
        p *= step
        out.append(_bar(f"t{i}", p, p * 1.001, p * 0.999, p))
    return out


def _mixed_regime(n):
    # alternating low-vol drift and a high-vol burst so folds span >=2 buckets incl high
    out, p = [], 50.0
    for i in range(n):
        if (i // 50) % 2 == 0:
            p *= 1.004
            h, l = p * 1.002, p * 0.998
        else:
            p *= (1.06 if i % 2 == 0 else 1 / 1.06)
            h, l = p * 1.03, p * 0.97
        out.append(_bar(f"t{i}", p, h, l, p))
    return out


def _tiny_cfg():
    # shrink the floors so synthetic fixtures can exercise the gates without 750+ real bars
    return GateAConfig(min_lookback_bars=200, k_folds=4, min_fold_bars=40,
                       min_total_trades=5, sanity_min_trades=2, dsr_min=0.0,
                       breadth_min_frac=0.5)


def _xover():
    return Strategy(name="x", symbol="X",
                    entry={"type": "sma_cross", "fast": 5, "slow": 10, "direction": "above"},
                    exit={"type": "sma_cross", "fast": 5, "slow": 10, "direction": "below"})


def test_screen_one_returns_report_with_m_recorded():
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert isinstance(rep, WalkForwardReport)
    assert rep.m_at_eval == 1
    assert rep.symbol_scope == "portfolio"


def test_screen_one_insufficient_history():
    bars = {"AAPL": _mixed_regime(120)}        # < min_lookback_bars 200
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert "insufficient_history" in rep.fail_codes and not rep.passed


def test_screen_one_single_regime_forces_coverage_failure():
    bars = {"AAPL": _rising(300), "MSFT": _rising(300)}   # one regime (up/low) only
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert "insufficient_regime_coverage" in rep.fail_codes and not rep.passed


def test_screen_one_breadth_rejects_single_symbol_edge():
    # AAPL edges up, MSFT/SPY/JPM chop sideways -> breadth < 0.5
    bars = {"AAPL": _mixed_regime(300), "MSFT": _flat(300), "SPY": _flat(300), "JPM": _flat(300)}
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert "breadth_fail" in rep.fail_codes and not rep.passed


def test_screen_one_cost_fragile_sign_flip_rejected():
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    heavy = stress_cost(CostConfig(slippage_pct=5.0))   # huge slippage flips a thin edge negative
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=heavy, m=1, require_cost_robust=True)
    assert "cost_fragile" in rep.fail_codes and not rep.passed


def test_screen_one_daily_only_flag():
    s = _xover().model_copy(update={"timeframe": "5m"})
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(s, bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert "daily_only_v1" in rep.fail_codes


def test_screen_one_ci_lb_negative_when_edge_is_a_coin_flip():
    # a chop series with no real edge -> pooled CI lower bound <= 0
    bars = {"AAPL": _chop(300), "MSFT": _chop(300)}
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert not rep.passed
    assert ("ci_lb_negative" in rep.fail_codes) or ("insufficient_sample" in rep.fail_codes)


# ---- Fix 3: the lockbox (leakage-prevention §6.3) failing branch must be scored on the producer ----
def _lb_bar(i, c):
    return _bar(f"t{i}", float(c), float(c) + 0.5, float(c) - 0.5, float(c))


def _losing_lockbox():
    # 20-bar warmup baseline, then a rise (5/10 cross -> entry) then a crash (cross below -> exit at a
    # loss). One closed trade, return ~ -1.9% -> holdout expectancy < 0.
    c = [100.0] * 22 + [100 + i * 2 for i in range(1, 12)] + [122 - i * 4 for i in range(1, 16)] + [60.0] * 6
    return [_lb_bar(i, v) for i, v in enumerate(c)]


def _winning_lockbox():
    # the same shape but a gentle post-entry decline -> exit ABOVE entry. One closed trade ~ +18.9%.
    c = [100.0] * 22 + [100 + i * 3 for i in range(1, 12)] + [133 - i for i in range(1, 14)] + [120.0] * 6
    return [_lb_bar(i, v) for i, v in enumerate(c)]


def test_screen_one_lockbox_fails_on_a_losing_holdout():
    main = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(_xover(), main, cfg=_tiny_cfg(), cost=NO_COST, m=1,
                     lockbox_bars_by_symbol={"AAPL": _losing_lockbox()})
    assert rep.lockbox is not None and rep.lockbox.evaluated is True
    assert rep.lockbox.num_trades >= 1
    assert rep.lockbox.expectancy is not None and rep.lockbox.expectancy < 0
    assert rep.lockbox.passed is False                       # the decisive failing branch


def test_screen_one_flags_pf_below_floor():
    # Fix 5: the declared-but-dead profit-factor floor is now wired. A chop series (mixed wins+losses,
    # pooled PF ~ between 1 and 2) trips pf_below_floor at pf_min=2.0 but not at pf_min=0.0.
    from dataclasses import replace
    bars = {"AAPL": _chop(300), "MSFT": _chop(300)}
    hot = screen_one(_xover(), bars, cfg=replace(_tiny_cfg(), pf_min=2.0), cost=NO_COST, m=1)
    assert "pf_below_floor" in hot.fail_codes and not hot.passed
    loose = screen_one(_xover(), bars, cfg=replace(_tiny_cfg(), pf_min=0.0), cost=NO_COST, m=1)
    assert "pf_below_floor" not in loose.fail_codes


def test_screen_one_applies_complexity_adjusted_edge_hurdle():
    # Fix 5: the OOS edge floor is scaled by 1 + 0.1*(num_leaves-1) — a single-leaf rule's hurdle is
    # the un-adjusted deflated floor; a 3-leaf rule's hurdle is 1.2x that base.
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    single = Strategy(name="s", symbol="X",
                      entry={"type": "sma_cross", "fast": 5, "slow": 10, "direction": "above"})
    multi = Strategy(name="m", symbol="X", exit={"type": "sma_cross", "fast": 5, "slow": 10,
                                                 "direction": "below"},
                     entry={"type": "all_of", "conditions": [
                         {"type": "sma_cross", "fast": 5, "slow": 10, "direction": "above"},
                         {"type": "rsi", "period": 14, "threshold": 40, "comparison": "above"}]})
    for s, expected_leaves in ((single, 1), (multi, 3)):
        rep = screen_one(s, bars, cfg=_tiny_cfg(), cost=NO_COST, m=5)
        assert rep.num_leaves == expected_leaves
        base = deflated_edge_floor(0.0, 5, max(1, rep.pooled_trades), k_defl=_tiny_cfg().k_defl)
        factor = 1.0 + _tiny_cfg().complexity_hurdle_per_leaf * (expected_leaves - 1)
        assert rep.edge_floor == pytest.approx(base * factor)


def test_gate_a_and_gate_b_share_one_portfolio_construction_helper():
    # Fix 4: Gate A (screen_one) and Gate B (trial.derive_portfolio) must build the basket portfolio
    # with the SAME helper so their DSR / drawdown thresholds are comparable.
    from webull_api.lab import gate_a as ga, trial as tr
    assert tr.sum_curves is ga.sum_curves


def test_sum_curves_time_union_and_carry_forward():
    from webull_api.lab.gate_a import sum_curves
    from webull_api.strategy.schema import EquityPoint
    a = [EquityPoint(time="d1", equity=100.0), EquityPoint(time="d3", equity=120.0)]
    b = [EquityPoint(time="d2", equity=50.0), EquityPoint(time="d3", equity=60.0)]
    out = sum_curves([a, b], base_equity=10.0)
    # union axis d1<d2<d3; carry-forward: at d1 b is pre-first (base 10) -> 110; at d2 a holds 100,
    # b=50 -> 150; at d3 a=120,b=60 -> 180.
    assert [(p.time, p.equity) for p in out] == [("d1", 110.0), ("d2", 150.0), ("d3", 180.0)]


def test_screen_one_lockbox_passes_on_a_profitable_holdout():
    main = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(_xover(), main, cfg=_tiny_cfg(), cost=NO_COST, m=1,
                     lockbox_bars_by_symbol={"AAPL": _winning_lockbox()})
    assert rep.lockbox is not None and rep.lockbox.evaluated is True
    assert rep.lockbox.expectancy is not None and rep.lockbox.expectancy > 0
    assert rep.lockbox.passed is True


def test_screen_one_no_cost_param_fragile_not_cost_fragile(monkeypatch):
    """param_fragile fires on NO_COST when param-neighbor median edge is non-positive;
    cost_fragile cannot fire because gross==net under NO_COST. Tests the param_fragile
    code path independently of cost_fragile (currently only cost_fragile is exercised).

    param_fragile is gated on `not fails` (screen_one line 411), so the base run must
    pass all other gates. _tiny_cfg() has graduate_max_dd_pct=-12.0 but the synthetic
    mixed-regime bars produce ~98% drawdown, causing dd_breach. We override the DD floor
    to -100% so the base passes and the param_neighbor block is reached.

    A breakout(lookback=200) neighbor on 300-bar data enters at most once and exits as
    'end_of_data' (no attributed trades), giving edges=[0.0]. median([0.0])=0.0<=0
    -> param_fragile fires."""
    from dataclasses import replace
    import webull_api.lab.gate_a as _ga
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    # Accept any drawdown so the base passes and we reach the param_neighbor check.
    cfg = replace(_tiny_cfg(), graduate_max_dd_pct=-100.0)
    # One neighbor that reliably produces 0 attributed trades (end_of_data exit only).
    zero_edge_nb = Strategy(name="z", symbol="X",
                            entry={"type": "breakout", "lookback": 200, "direction": "high"})
    monkeypatch.setattr(_ga, "param_neighbors", lambda s: [zero_edge_nb])
    rep = screen_one(_xover(), bars, cfg=cfg, cost=NO_COST, m=1, require_cost_robust=True)
    # With NO_COST: gross_total == net_total, so cost_fragile can never fire.
    assert "cost_fragile" not in rep.fail_codes
    # The stub neighbor has 0 attributed trades -> edge=0.0 -> median=0.0 <= 0 -> param_fragile.
    assert rep.param_neighbor_median_edge is not None and rep.param_neighbor_median_edge <= 0
    assert "param_fragile" in rep.fail_codes and not rep.passed


# ---- Fix: E[max Sharpe] uses the Bailey & Lopez de Prado expression, not sqrt(2 ln m) ----
def test_expected_max_sharpe_matches_bailey_lopez_de_prado():
    import math
    import statistics as stats
    from webull_api.lab.gate_a import _expected_max_sharpe
    g = 0.5772156649015329
    nd = stats.NormalDist()
    for m in (2, 10, 100, 100000):
        want = (1 - g) * nd.inv_cdf(1 - 1 / m) + g * nd.inv_cdf(1 - 1 / (m * math.e))
        assert _expected_max_sharpe(m) == pytest.approx(want)
    assert _expected_max_sharpe(10) == pytest.approx(1.5746, abs=1e-3)   # published-formula value
    assert _expected_max_sharpe(1) == 0.0
    # strictly below the old leading-order bound (which over-deflated ~37% at m=10)
    assert _expected_max_sharpe(10) < math.sqrt(2 * math.log(10))
    # still monotone increasing in m (the DSR monotonicity tests pin the downstream effect)
    assert _expected_max_sharpe(2) < _expected_max_sharpe(10) < _expected_max_sharpe(100)


# ---- Fix: the lockbox holdout runs on the PRODUCTION path (no caller ever passed one) ----
def test_screen_one_carves_a_default_lockbox_holdout():
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(_xover(), bars, cfg=_tiny_cfg(), cost=NO_COST, m=1)
    assert rep.lockbox is not None and rep.lockbox.evaluated is True   # never None by default
    # and the per-symbol screened span is recorded for the verdict's forward scaling
    n_lb = int(300 * _tiny_cfg().lockbox_frac)
    warmup = _warmup_bars(_xover(), _tiny_cfg())
    assert rep.screened_bars == pytest.approx(300 - n_lb - warmup)


def test_screen_one_default_carve_equals_explicit_tail_lockbox():
    # The carve must be exactly "screen the remainder, score the reserved tail": screening the
    # first 255 bars with the tail passed explicitly reproduces the default report byte-for-byte.
    full = _mixed_regime(300)
    cfg = _tiny_cfg()
    n_lb = int(300 * cfg.lockbox_frac)
    warmup = _warmup_bars(_xover(), cfg)
    carved = screen_one(_xover(), {"AAPL": full}, cfg=cfg, cost=NO_COST, m=1)
    explicit = screen_one(_xover(), {"AAPL": full[:300 - n_lb]}, cfg=cfg, cost=NO_COST, m=1,
                          lockbox_bars_by_symbol={"AAPL": full[300 - n_lb - warmup:]})
    assert carved.model_dump() == explicit.model_dump()


def test_screen_one_lockbox_frac_zero_disables_the_carve():
    from dataclasses import replace
    bars = {"AAPL": _mixed_regime(300), "MSFT": _mixed_regime(300)}
    rep = screen_one(_xover(), bars, cfg=replace(_tiny_cfg(), lockbox_frac=0.0), cost=NO_COST, m=1)
    assert rep.lockbox is None
