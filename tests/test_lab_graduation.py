"""Fix 1 (CRITICAL) regression: confirmed_proven must be reachable by RUNTIME code.

`regime_buckets_seen` is the field that separates provisional_proven from confirmed_proven in
verdict.evaluate_verdict. Before this fix it was populated only by tests, so every trial was stuck at
provisional_proven forever and lab_service.promote (which requires confirmed_proven) could never graduate.

These tests drive the REAL open_trial -> advance pipeline through the REAL evaluate_verdict (no
monkeypatch): a forward window spanning >= 2 distinct regime buckets reaches confirmed_proven; a
single-regime forward window stays provisional_proven; and lab_service.promote then graduates it.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import replace

from webull_api.lab import trial, verdict
from webull_api.lab.schema import (DEFAULT_GATE_B_CONFIG, ProvingState, StrategyRecord,
                                   WalkForwardReport)
from webull_api.strategy.cost import NO_COST
from webull_api.strategy.schema import Strategy
from webull_web import lab_service, lab_store


def _strat():
    return Strategy(name="grad", symbol="SPY", take_profit_pct=3.0, starting_equity=10000.0,
                    entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})


def _bars(closes, start="2026-01-02"):
    d = dt.date.fromisoformat(start)
    out = []
    for i, c in enumerate(closes):
        pad = max(1.0, abs(c) * 0.01)
        out.append({"time": (d + dt.timedelta(days=i)).isoformat(), "open": float(c),
                    "high": float(c) + pad, "low": float(c) - pad, "close": float(c),
                    "volume": 1_000_000.0})
    return out


def _saw(n, p, up=2.0, dip=1.0, period=3):
    """A low-vol rising sawtooth (+up,+up,-dip) so the 2/3 SMA cross fires repeatedly."""
    out = []
    for i in range(n):
        p = p - dip if (i % period == period - 1) else p + up
        out.append(round(p, 4))
    return out


def _high_vol_rising(n, p):
    """A high-vol rising series (±~6% alternating) so the trailing-window realized vol flips to high."""
    out = []
    for i in range(n):
        p = p * 1.06 if i % 2 == 0 else p * 0.985
        out.append(round(p, 4))
    return out


# A relaxed Gate-B config that keeps every threshold EXCEPT the regime-bucket count loose, so these
# tests isolate the regime-bucket -> provisional/confirmed distinction (mirrors test_lab_verdict's
# `replace(..., dsr_min=-1.0)` pattern — a config param, NOT a monkeypatch of the verdict logic).
_CFG_B = replace(DEFAULT_GATE_B_CONFIG, dsr_min=-1.0, min_forward_bars=1, min_forward_trades=1,
                 min_forward_trades_floor=1, min_profitable_window_frac=0.0, rolling_window_bars=5)


def _gate_a():
    return WalkForwardReport(passed=True, pooled_expectancy=0.0, expected_trades_over_window=1.0,
                             max_drawdown_pct=0.0, lockbox=None)


def _open():
    # A gently-rising warmup -> up/low at open — the SAME bucket the low-vol rising saw forward
    # window continues in. (The regime short-slice fix made trend detection real: a rising window
    # now correctly reads "up", so the old flat warmup would seed side/low and the rising forward
    # window would ADD up/low — two buckets — breaking the single-regime scenario's premise.)
    warm = _bars([100.0 + 0.2 * i for i in range(14)], start="2026-01-02")
    return trial.open_trial(_strat(), {"SPY": warm}, trial_id="grad", now_iso="2026-01-15T00:00:00",
                            inception_et_date="2099-01-01", gate_a=_gate_a(), basket=["SPY"])


def test_open_trial_seeds_initial_forward_regime_bucket():
    st = _open()
    assert st.regime_buckets_seen == ["up/low"]              # RUNTIME-populated, not by a test


def test_single_regime_forward_window_stays_provisional():
    st = _open()
    st, n = trial.advance(st, {"SPY": _bars(_saw(30, 104.0), start="2026-01-16")},
                          now_iso="2026-02-15T00:00:00", today_et="2099-01-01", cost=NO_COST)
    assert n == 30
    assert st.regime_buckets_seen == ["up/low"]              # one bucket only
    assert st.book.forward_trades >= 1 and st.book.metrics.max_drawdown_pct > -12.0
    assert verdict.evaluate_verdict(st, cfg_b=_CFG_B, m=10) == "provisional_proven"


def test_two_regime_forward_window_reaches_confirmed_proven():
    st = _open()
    st, _ = trial.advance(st, {"SPY": _bars(_saw(30, 104.0), start="2026-01-16")},
                          now_iso="2026-02-14T00:00:00", today_et="2099-01-01", cost=NO_COST)
    assert verdict.evaluate_verdict(st, cfg_b=_CFG_B, m=10) == "provisional_proven"   # still 1 bucket

    hi = _high_vol_rising(30, st.bars_by_symbol["SPY"][-1]["close"])
    st, _ = trial.advance(st, {"SPY": _bars(hi, start="2026-02-15")},
                          now_iso="2026-04-01T00:00:00", today_et="2099-01-01", cost=NO_COST)
    assert len(set(st.regime_buckets_seen)) >= 2             # a second regime bucket accrued
    assert verdict.evaluate_verdict(st, cfg_b=_CFG_B, m=10) == "confirmed_proven"


def test_lab_service_promote_graduates_a_confirmed_trial(tmp_path, monkeypatch):
    """The end of the headline path: a confirmed_proven trial built by the REAL pipeline is graduated
    by lab_service.promote into a PaperProvenRecord on the shelf."""
    monkeypatch.setenv("LAB_DIR", str(tmp_path / "lab"))

    # Build a confirmed_proven state via the real engines (two regimes).
    st = _open()
    st, _ = trial.advance(st, {"SPY": _bars(_saw(30, 104.0), start="2026-01-16")},
                          now_iso="2026-02-14T00:00:00", today_et="2099-01-01", cost=NO_COST)
    hi = _high_vol_rising(30, st.bars_by_symbol["SPY"][-1]["close"])
    st, _ = trial.advance(st, {"SPY": _bars(hi, start="2026-02-15")},
                          now_iso="2026-04-01T00:00:00", today_et="2099-01-01", cost=NO_COST)
    v = verdict.evaluate_verdict(st, cfg_b=_CFG_B, m=10)
    assert v == "confirmed_proven"

    st.status = v
    lab_store.save_trial(st)
    rec = StrategyRecord(id="grad", strategy=_strat(), fingerprint="fp-grad", canon_bucket="cb",
                         archetype="trend_follow", cohort="trend_follow:side/low",
                         status="confirmed_proven", created_at_iso="2026-01-01", as_of="2026-01-01",
                         trial_id="grad", gate_a=_gate_a())
    lab_store.upsert_record(rec)

    promo = lab_service.promote(["grad"])
    assert promo["promoted"] == ["grad"] and promo["refused"] == []
    proven = lab_store.load_proven()
    assert len(proven) == 1 and proven[0].verdict == "confirmed_proven"
    assert proven[0].human_promotion_authorized is False     # the human gate stays pre-installed OFF


def test_isinstance_proving_state():
    assert isinstance(_open(), ProvingState)


# Ported 2026-09-28 from the archived POST /api/lab/candidates/{id}/promote|reject route tests (their only coverage).
def _record(status: str) -> StrategyRecord:
    return StrategyRecord(id="a", strategy=_strat(), fingerprint="fp-a", canon_bucket="cb",
                          archetype="trend_follow", cohort="trend_follow:up/low", status=status,
                          created_at_iso="2026-06-29T00:00:00Z", as_of="2026-06-29", trial_id="a")


def test_lab_service_promote_refuses_a_record_that_is_not_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path / "lab"))
    lab_store.save_library([_record("proving")])
    promo = lab_service.promote(["a"])
    assert promo["promoted"] == [] and promo["refused"][0]["id"] == "a"
    assert lab_store.load_proven() == []          # nothing written to the proven shelf


def test_lab_service_reject_marks_the_record_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path / "lab"))
    lab_store.save_library([_record("proving")])
    assert lab_service.reject(["a"]) == {"rejected": ["a"]}
    assert lab_store.get_record("a").status == "rejected"
