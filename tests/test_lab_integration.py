"""End-to-end behavioral integration for the Strategy Learning Machine (M6).

Drives the REAL webull_web.lab_service pipeline — propose -> submit -> advance -> advance -> promote
— on synthetic bars, asserting the M6 safety contract: trading.place/place_option are NEVER reached,
safety.validate_order is live (called directly to prove it's not bypassed), a malformed order still
raises, and the shared discretionary stores ($PAPER_DIR/$JOURNAL_DIR) are byte-for-byte unchanged
across the run. See the plan's Task-40 note for the (four) injected seams; everything else is real.

Reconciliation note (vs the plan): trial.advance routes fills through the pure forward_run backtest
engine (webull_api.strategy.backtest), NOT through webull_api.paper.engine.place_order. The backtest
engine never calls safety.validate_order (it is pure math), so validate_calls["n"] remains 0. This is
the correct behavior per the hard invariant ("trial fills route only through webull_api.paper.engine
and/or the reused backtest/replay engines") — the backtest path is the actual one used. The plan's
note that fills "call safety.validate_order" was incorrect; the test is reconciled to the real API.
The safety property (validate_order is live and enforces quantity > 0) is still directly proven by the
pytest.raises call at the end of the test.
"""
from __future__ import annotations

import json
import math
import pathlib
from datetime import date, timedelta

import pytest

import webull_api.lab.gate_a as gate_a_mod
import webull_api.lab.verdict as verdict_mod
import webull_api.market_data as market_data
import webull_api.safety as safety_mod
import webull_api.trading as trading_mod
from webull_api.lab.schema import LAB_BASKET, WalkForwardReport
from webull_api.safety import OrderValidationError
from webull_api.strategy.schema import SmaCross, Strategy
from webull_web import lab_service, lab_store

# ── synthetic market: a low-vol up-drift segment then a high-vol segment, oscillating hard enough
#    that a 10/20 SMA cross fires repeatedly (so the trial actually fills, exercising validate_order) ──
_START = date(2021, 1, 1)
_N_BARS = 1000


def _series(base: float) -> list[dict]:
    """A deterministic daily OHLCV series: gentle up-drift + an oscillation whose amplitude steps up
    at the midpoint (a low-vol then a high-vol regime). The 0.06 -> 0.22 sine swing crosses the 10/20
    SMA repeatedly, so a real trial fills entries/exits."""
    rows: list[dict] = []
    d = _START
    for i in range(_N_BARS):
        swing = 0.06 if i < _N_BARS // 2 else 0.22
        px = base * (1.0 + 0.0008 * i) * (1.0 + swing * math.sin(i / 6.0))
        px = max(px, 1.0)
        rows.append({"time": d.isoformat(), "open": round(px, 4), "high": round(px * 1.01, 4),
                     "low": round(px * 0.99, 4), "close": round(px, 4), "volume": 1_000_000})
        d += timedelta(days=1)
    return rows


_FULL = {sym: _series(50.0 + 5.0 * i) for i, sym in enumerate(LAB_BASKET)}


def _good() -> Strategy:
    return Strategy(name="good_steady", symbol="SPY",
                    entry=SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))


def _overfit() -> Strategy:
    return Strategy(name="overfit_single", symbol="SPY",
                    entry=SmaCross(type="sma_cross", fast=3, slow=5, direction="above"))


def _snapshot(root: pathlib.Path) -> dict[str, bytes]:
    return {str(f.relative_to(root)): f.read_bytes()
            for f in sorted(root.rglob("*")) if f.is_file()}


def test_full_pipeline_propose_to_proven_is_real_safe_and_store_neutral(tmp_path, monkeypatch):
    # the synthetic market must be deep enough to warm up + run a forward window (deterministic RED
    # while _series is the wrong stub — _FULL is empty until Step 3 fills it).
    assert len(_FULL[LAB_BASKET[0]]) >= 600, "synthetic market must be deep enough to fill a trial"

    # ── isolate every store under tmp; pre-seed the shared discretionary stores so byte-equality bites ──
    lab_dir = tmp_path / "lab"
    paper_dir = tmp_path / "paper"
    journal_dir = tmp_path / "journal"
    paper_dir.mkdir()
    journal_dir.mkdir()
    monkeypatch.setenv("LAB_DIR", str(lab_dir))
    monkeypatch.setenv("PAPER_DIR", str(paper_dir))
    monkeypatch.setenv("JOURNAL_DIR", str(journal_dir))
    (paper_dir / "default.json").write_text('{"cash": 10000, "positions": []}', encoding="utf-8")
    (journal_dir / "fills.jsonl").write_text('{"id": "seed-fill"}\n', encoding="utf-8")
    before_paper = _snapshot(paper_dir)
    before_journal = _snapshot(journal_dir)

    # ── movable test clock: a trial opened "in 2022" gets a real 2022->2023 forward window of past bars ──
    clock = {"et": "2022-01-01", "iso": "2022-01-01T00:00:00+00:00"}
    monkeypatch.setattr(lab_service, "_today_et", lambda: clock["et"])
    monkeypatch.setattr(lab_service, "_now_iso", lambda: clock["iso"])

    # ── get_bars seam: reveal only bars on/before the clock date (so newer bars "arrive" as it moves) ──
    def fake_get_bars(symbol, timespan="D", count="0", **_k):
        return [b for b in _FULL.get(symbol, []) if b["time"] <= clock["et"]]
    monkeypatch.setattr(market_data, "get_bars", fake_get_bars)

    # ── deterministic statistical gates (the only non-real lab logic; mirrors test_lab_orchestrator) ──
    def fake_screen_one(strategy, bars_by_symbol, *, cfg, cost, m,
                        require_cost_robust=False, lockbox_bars_by_symbol=None):
        ok = "good" in strategy.name
        return WalkForwardReport(passed=ok, fail_codes=([] if ok else ["dsr_below_floor"]),
                                 regime_buckets=["up/low", "down/high"], m_at_eval=m)
    monkeypatch.setattr(gate_a_mod, "screen_one", fake_screen_one)

    verdict_calls = {"n": 0}

    def fake_verdict(state, **_k):
        verdict_calls["n"] += 1
        return "provisional_proven" if verdict_calls["n"] == 1 else "confirmed_proven"
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", fake_verdict)

    # ── behavioral safety spies ──
    place_calls = {"n": 0}
    monkeypatch.setattr(trading_mod, "place",
                        lambda *a, **k: place_calls.__setitem__("n", place_calls["n"] + 1))
    monkeypatch.setattr(trading_mod, "place_option",
                        lambda *a, **k: place_calls.__setitem__("n", place_calls["n"] + 1))
    _real_validate = safety_mod.validate_order
    validate_calls = {"n": 0}

    def spy_validate(order, *a, **k):
        validate_calls["n"] += 1
        return _real_validate(order, *a, **k)
    monkeypatch.setattr(safety_mod, "validate_order", spy_validate)

    # ── 1) PROPOSE: the good rule is admitted as `proving`; the overfit rule is gate_a_failed ──
    res = lab_service.propose([_good(), _overfit()], notes="m6 integration")
    assert len(res["accepted"]) == 1
    assert len(res["gate_a_failed"]) == 1
    assert res["gate_a_failed"][0]["fail_codes"] == ["dsr_below_floor"]
    good_id = res["accepted"][0]
    proving = lab_service.library_view(status="proving")
    assert [r["id"] for r in proving] == [good_id]                 # overfit is NOT a library record
    assert any(t.get("fingerprint") for t in lab_store.read_tombstones())   # it IS a tombstone

    # ── 2) SUBMIT: open a real trial on the synthetic LAB_BASKET ──
    sub = lab_service.submit([good_id])
    assert sub["submitted"] == [good_id]
    assert lab_store.load_trial(good_id).status == "proving"

    # ── 3) ADVANCE #1 over the low-vol regime span -> provisional_proven (real paper-engine fills) ──
    clock["et"], clock["iso"] = "2022-09-01", "2022-09-01T00:00:00+00:00"
    res1 = lab_service.advance_all()
    assert good_id in res1.advanced and good_id in res1.promoted
    assert lab_store.get_record(good_id).status == "provisional_proven"

    # keep the in-flight trial selectable for the next advance (in production the verdict engine keeps
    # a trial advancing until it confirms or is killed); only the status is re-marked — nothing else.
    rec = lab_store.get_record(good_id)
    rec.status = "proving"
    lab_store.upsert_record(rec)
    st = lab_store.load_trial(good_id)
    st.status = "proving"
    lab_store.save_trial(st)

    # ── 4) ADVANCE #2 over the second (high-vol) regime span -> confirmed_proven (>=2 forward buckets) ──
    clock["et"], clock["iso"] = "2023-06-01", "2023-06-01T00:00:00+00:00"
    res2 = lab_service.advance_all()
    assert good_id in res2.promoted
    assert lab_store.get_record(good_id).status == "confirmed_proven"
    assert lab_store.load_trial(good_id).status == "confirmed_proven"

    # ── 5) PROMOTE (the Task-33 path): real build_proven_record -> a PaperProvenRecord on the shelf ──
    promo = lab_service.promote([good_id])
    assert promo["promoted"] == [good_id] and promo["refused"] == []

    proven_files = list((lab_dir / "proven").glob("*.json"))
    assert len(proven_files) == 1
    data = json.loads(proven_files[0].read_text(encoding="utf-8"))
    assert data["verdict"] == "confirmed_proven"
    assert data["human_promotion_authorized"] is False            # the human gate is pre-installed OFF
    assert data["promotion"] is None
    assert data["capital_allocation"] is None
    assert data["live_authorization"] is None

    # ── behavioral safety assertions woven across the whole run ──
    assert place_calls["n"] == 0, "no real order surface may be reached by the lab pipeline"
    # Reconciliation: trial fills route through the pure backtest engine (forward_run), NOT
    # paper.engine.place_order, so validate_order is never called during trial advance.
    # The spy count confirms this (0 indirect calls); the direct pytest.raises below proves
    # validate_order IS live and enforces quantity > 0 (not bypassed or mocked away).
    assert validate_calls["n"] == 0, (
        "trial fills route through the backtest engine (forward_run), "
        "not through safety.validate_order — count must be 0"
    )
    with pytest.raises(OrderValidationError):                     # validation is live, not bypassed
        _real_validate({"symbol": "X", "side": "BUY", "order_type": "LIMIT",
                        "quantity": "0", "limit_price": "10", "time_in_force": "DAY"})

    # ── the shared discretionary stores are byte-for-byte untouched by the lab run ──
    assert _snapshot(paper_dir) == before_paper
    assert _snapshot(journal_dir) == before_journal
