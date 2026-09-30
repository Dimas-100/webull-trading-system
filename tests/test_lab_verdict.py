from __future__ import annotations

import math
from dataclasses import replace

import pytest

from webull_api.lab.schema import (DEFAULT_GATE_A_CONFIG, DEFAULT_GATE_B_CONFIG, BacktestResult,
                                    LockboxResult, ProvingState, TrialBook, WalkForwardReport)
from webull_api.strategy.schema import EquityPoint, Strategy, Trade
from webull_api.lab import verdict


def _strat():
    return Strategy(name="t", symbol="SPY",
                    entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})


def _trade(pnl):
    return Trade(entry_time="2026-01-02", entry_price=100.0, exit_time="2026-01-05",
                 exit_price=100.0 + pnl, shares=1.0, pnl=float(pnl),
                 return_pct=float(pnl), exit_reason="signal")


def _curve(values):
    return [EquityPoint(time=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", equity=float(v))
            for i, v in enumerate(values)]


def _book(*, trades, curve, max_dd, expectancy, forward_trades, forward_bars):
    metrics = BacktestResult(total_return_pct=0.0, buy_hold_return_pct=0.0, num_trades=len(trades),
                             win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0, expectancy=expectancy,
                             max_drawdown_pct=max_dd, equity_curve=curve, trades=trades)
    return TrialBook(scope="portfolio", starting_equity=10000.0, equity_curve=curve, metrics=metrics,
                     trades_by_symbol={"SPY": trades}, per_symbol_metrics={"SPY": metrics},
                     forward_trades=forward_trades, forward_bars=forward_bars)


def _gate_a():
    return WalkForwardReport(passed=True, pooled_expectancy=40.0, expected_trades_over_window=20.0,
                             max_drawdown_pct=-10.0, lockbox=LockboxResult(evaluated=True, passed=True))


def _state(book, *, buckets, status="proving"):
    return ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                        inception_et_date="2026-01-01", started_at_iso="2026-01-01T00:00:00",
                        gate_a=_gate_a(), book=book, regime_buckets_seen=buckets, status=status)


# ---- helpers ----
def test_rolling_window_profit_frac_all_up():
    assert verdict.rolling_window_profit_frac(_curve([100, 110, 120, 130, 140]), window=2) == 1.0


def test_rolling_window_profit_frac_all_down():
    assert verdict.rolling_window_profit_frac(_curve([140, 130, 120, 110, 100]), window=2) == 0.0


def test_decay_ok_envelope_and_sign():
    assert verdict.decay_ok(8.0, 10.0) is True       # within 50% envelope
    assert verdict.decay_ok(-1.0, 10.0) is False     # sign flip
    assert verdict.decay_ok(2.0, 10.0) is False      # below 50% of backtest (5.0)


def test_regime_buckets_in_forward_dedups():
    st = _state(_book(trades=[], curve=_curve([100]), max_dd=0.0, expectancy=0.0,
                      forward_trades=0, forward_bars=0), buckets=["up/low", "up/low", "down/high"])
    assert verdict.regime_buckets_in_forward(st) == ["down/high", "up/low"]


# ---- evaluate_verdict ----
def _passing_book():
    return _book(trades=[_trade(50.0)] * 15, curve=_curve([10000 + 25 * i for i in range(150)]),
                 max_dd=-5.0, expectancy=45.0, forward_trades=15, forward_bars=150)


def test_evaluate_verdict_one_bucket_is_provisional():
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0)   # decouple from M2's dsr magnitude
    st = _state(_passing_book(), buckets=["up/low"])
    assert verdict.evaluate_verdict(st, cfg_b=cfg_b, m=10) == "provisional_proven"


def test_evaluate_verdict_two_buckets_is_confirmed():
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0)
    st = _state(_passing_book(), buckets=["up/low", "down/high"])
    assert verdict.evaluate_verdict(st, cfg_b=cfg_b, m=10) == "confirmed_proven"


def test_evaluate_verdict_kills_on_drawdown_breach_at_any_n():
    # max_dd -25 <= kill -20: rejected regardless of small sample
    st = _state(_book(trades=[_trade(1.0)] * 3, curve=_curve([10000, 9000, 7000]),
                      max_dd=-25.0, expectancy=1.0, forward_trades=3, forward_bars=20), buckets=[])
    assert verdict.evaluate_verdict(st, m=5) == "rejected"


def test_evaluate_verdict_kills_on_proven_negative_edge():
    # 25 negative trades -> CI upper bound < 0, forward_trades >= min_kill_sample(20)
    st = _state(_book(trades=[_trade(-50.0)] * 25, curve=_curve([10000 - 10 * i for i in range(30)]),
                      max_dd=-8.0, expectancy=-50.0, forward_trades=25, forward_bars=140), buckets=[])
    assert verdict.evaluate_verdict(st, m=5) == "rejected"


def test_evaluate_verdict_below_floor_keeps_proving():
    st = _state(_book(trades=[_trade(5.0)] * 2, curve=_curve([10000, 10010, 10020]),
                      max_dd=-2.0, expectancy=5.0, forward_trades=2, forward_bars=20), buckets=["up/low"])
    assert verdict.evaluate_verdict(st, m=5) == "proving"


def test_evaluate_verdict_respects_stale_data_flag():
    st = _state(_passing_book(), buckets=["up/low", "down/high"], status="stale_data")
    assert verdict.evaluate_verdict(st, m=5) == "stale_data"


def test_evaluate_verdict_blocks_proven_when_lockbox_failed():
    # Fix 3 (consumer side, §6.3): an otherwise-PASSING book whose Gate-A lockbox holdout LOST must
    # NOT graduate — lockbox_ok gates the PASS predicate, so no *_proven verdict is returned.
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0)
    gate_a = WalkForwardReport(passed=True, pooled_expectancy=40.0, expected_trades_over_window=20.0,
                               max_drawdown_pct=-10.0,
                               lockbox=LockboxResult(evaluated=True, expectancy=-2.0, passed=False))
    st = ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                      inception_et_date="2026-01-01", started_at_iso="2026-01-01T00:00:00",
                      gate_a=gate_a, book=_passing_book(),
                      regime_buckets_seen=["up/low", "down/high"], status="proving")
    v = verdict.evaluate_verdict(st, cfg_b=cfg_b, m=10)
    assert v == "proving"
    assert v not in ("provisional_proven", "confirmed_proven")


from webull_api.lab.schema import PaperProvenRecord, StrategyRecord


def _record():
    return StrategyRecord(id="rec1", strategy=_strat(), fingerprint="fp1", canon_bucket="cb1",
                          archetype="trend_follow", cohort="trend_follow:up/low",
                          created_at_iso="2026-01-01T00:00:00", as_of="2026-06-01",
                          trial_id="t1", generation=2)


def test_build_proven_record_emits_pre_installed_human_gate():
    st = _state(_passing_book(), buckets=["up/low", "down/high"], status="confirmed_proven")
    rec = _record()
    pr = verdict.build_proven_record(rec, st, graduated_at_iso="2026-06-29T00:00:00", m_at_graduation=42)
    assert isinstance(pr, PaperProvenRecord)
    assert pr.verdict == "confirmed_proven"
    assert pr.strategy.model_dump() == _strat().model_dump()        # frozen rule snapshot only
    # the LATER real-money spec is the SOLE writer of these — hard-set here:
    assert pr.promotion is None and pr.capital_allocation is None and pr.live_authorization is None
    assert pr.human_promotion_authorized is False
    assert pr.m_at_graduation == 42
    assert pr.gate_b["trial_id"] == "t1"
    assert "UPPER BOUND" in pr.assumptions["note"]


def test_build_proven_record_refuses_non_confirmed_state():
    st = _state(_passing_book(), buckets=["up/low"], status="provisional_proven")
    with pytest.raises(ValueError):
        verdict.build_proven_record(_record(), st, graduated_at_iso="x", m_at_graduation=1)


# ---- #4: the forward DSR is DECOUPLED from the Gate-A search count (cfg_b.forward_dsr_m, default 1):
# the forward window is a single out-of-sample CONFIRMATION of an already-selected strategy, so it is
# NOT re-penalized by the ever-growing m_at_open. A constant also means a granted trial can never
# demote as the lab-global M grows. forward_dsr_m=None restores the fully-coupled max-rigor mode. ----
def _dsr_sensitive_curve(n=200, mu=0.0014, amp=0.007, base=10000.0):
    """A shallow, positive, low-vol forward curve whose DSR clears 0.95 under the small confirmatory
    count but falls far below it when coupled to a large m (dsr(m=1)~0.99, dsr(m=50000)~0.28,
    max_dd ~ -1%) — so the forward_dsr_m mode is the binding gate."""
    eq = [base]
    for i in range(n):
        eq.append(eq[-1] * (1 + mu + amp * math.sin(i)))
    return [EquityPoint(time=f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", equity=v)
            for i, v in enumerate(eq)]


def _dsr_book():
    return _book(trades=[_trade(50.0)] * 15, curve=_dsr_sensitive_curve(), max_dd=-1.03,
                expectancy=45.0, forward_trades=15, forward_bars=200)


def test_forward_verdict_is_decoupled_from_global_M():
    # DEFAULT forward_dsr_m=1: the forward DSR ignores the passed global m entirely, so a granted
    # trial can never demote when the lab-global M balloons because unrelated candidates ran.
    st = _state(_dsr_book(), buckets=["up/low", "down/high"])
    st.m_at_open = 5
    assert verdict.evaluate_verdict(st, m=5) == "confirmed_proven"
    assert verdict.evaluate_verdict(st, m=50000) == "confirmed_proven"
    assert verdict.evaluate_verdict(st, m=10**9) == "confirmed_proven"


def test_high_m_at_open_still_graduates_under_default_decoupling():
    # #4's whole point: a trial OPENED at a high multiple-testing count STILL graduates under the
    # default — graduation no longer demands an implausible Sharpe just because many OTHER strategies
    # were backtested before it (that burden was paid at Gate-A).
    late = _state(_dsr_book(), buckets=["up/low", "down/high"])
    late.m_at_open = 50000
    assert verdict.evaluate_verdict(late, m=5) == "confirmed_proven"


def test_coupled_max_rigor_mode_still_available():
    # Opt back into the fully-coupled behavior (forward_dsr_m=None): the forward DSR is then deflated
    # by the frozen m_at_open, so the same high-count trial is (correctly) NOT granted, while an early
    # (small m_at_open) trial still graduates — the strict path remains reachable via config.
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, forward_dsr_m=None)
    late = _state(_dsr_book(), buckets=["up/low", "down/high"])
    late.m_at_open = 50000
    assert verdict.evaluate_verdict(late, cfg_b=cfg_b, m=5) == "proving"
    early = _state(_dsr_book(), buckets=["up/low", "down/high"])
    early.m_at_open = 5
    assert verdict.evaluate_verdict(early, cfg_b=cfg_b, m=5) == "confirmed_proven"


# ---- Fix: the decay gate compares LIKE units (% vs %) — it used to compare the forward book's
# DOLLAR expectancy against Gate-A's PERCENT pooled_expectancy, degenerating into a sign check. ----
def _unit_trade(pnl, ret):
    return Trade(entry_time="2026-01-02", entry_price=100000.0, exit_time="2026-01-05",
                 exit_price=100000.0 + pnl, shares=1.0, pnl=float(pnl),
                 return_pct=float(ret), exit_reason="signal")


def _unit_state(ret_pct):
    trades = [_unit_trade(500.0, ret_pct)] * 15          # big $ pnl, controlled % return
    book = _book(trades=trades, curve=_curve([10000 + 25 * i for i in range(150)]),
                 max_dd=-5.0, expectancy=500.0, forward_trades=15, forward_bars=150)
    ga = WalkForwardReport(passed=True, pooled_expectancy=2.0, expected_trades_over_window=20.0,
                           max_drawdown_pct=-10.0, lockbox=LockboxResult(evaluated=True, passed=True))
    return ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                        inception_et_date="2026-01-01", started_at_iso="2026-01-01T00:00:00",
                        gate_a=ga, book=book, regime_buckets_seen=["up/low", "down/high"],
                        status="proving")


def test_decay_gate_compares_percent_not_dollars():
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0)
    # forward +0.5% vs Gate-A +2.0% -> below the 50% envelope -> keeps proving. A $-comparison
    # would have passed trivially ($500/trade vs "2.0") — this pins the unit fix.
    assert verdict.evaluate_verdict(_unit_state(0.5), cfg_b=cfg_b, m=10) == "proving"
    # control: the SAME dollar book with a healthy % return (1.5% >= 50% of 2.0%) graduates,
    # so the decay gate is the only discriminating condition above.
    assert verdict.evaluate_verdict(_unit_state(1.5), cfg_b=cfg_b, m=10) == "confirmed_proven"


# ---- Fix: expected_trades_over_window is a PER-SYMBOL full-lookback count; the adaptive floor
# now scales the POOLED Gate-A trade count to the observed forward window via screened_bars. ----
def test_adaptive_floor_scales_expected_trades_to_forward_window():
    cfg_b = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0)
    # an infrequent rule: 30 pooled trades over a 740-bar screened span -> over the observed
    # 140-bar forward window only ~5 trades are expected -> floor drops to the hard floor (8),
    # and a 9-trade forward book passes.
    trades = [_trade(50.0)] * 9
    book = _book(trades=trades, curve=_curve([10000 + 25 * i for i in range(150)]),
                 max_dd=-5.0, expectancy=45.0, forward_trades=9, forward_bars=140)
    ga = WalkForwardReport(passed=True, pooled_expectancy=40.0, pooled_trades=30,
                           screened_bars=740.0, expected_trades_over_window=15.0,
                           max_drawdown_pct=-10.0, lockbox=LockboxResult(evaluated=True, passed=True))
    st = ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                      inception_et_date="2026-01-01", started_at_iso="2026-01-01T00:00:00",
                      gate_a=ga, book=book, regime_buckets_seen=["up/low", "down/high"],
                      status="proving")
    assert verdict.evaluate_verdict(st, cfg_b=cfg_b, m=10) == "confirmed_proven"
    # legacy report (no screened_bars): the raw per-symbol figure is consumed unscaled ->
    # floor stays at min_forward_trades (12) -> the same 9-trade book keeps proving.
    st_legacy = ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                             inception_et_date="2026-01-01", started_at_iso="2026-01-01T00:00:00",
                             gate_a=ga.model_copy(update={"screened_bars": 0.0}), book=book,
                             regime_buckets_seen=["up/low", "down/high"], status="proving")
    assert verdict.evaluate_verdict(st_legacy, cfg_b=cfg_b, m=10) == "proving"
