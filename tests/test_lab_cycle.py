import math

from webull_api.lab import cycle, dedup
from webull_api.lab.schema import (DEFAULT_GATE_A_CONFIG, DEFAULT_LAB_CONFIG, IngestResult,
                                   ReseedResult, SeedEntry, StagnationState, StrategyRecord,
                                   TrialBook, WalkForwardReport)
from webull_api.strategy.schema import BacktestResult, EquityPoint, SmaCross, Strategy

# synthetic daily series deep enough that trial.open_trial never hits NotEnoughData
import datetime as _dt


def _series(n=400):
    rows, d = [], _dt.date(2021, 1, 1)
    for i in range(n):
        px = max(50.0 * (1 + 0.0008 * i) * (1 + 0.08 * math.sin(i / 6.0)), 1.0)
        rows.append({"time": d.isoformat(), "open": round(px, 4), "high": round(px * 1.01, 4),
                     "low": round(px * 0.99, 4), "close": round(px, 4), "volume": 1_000_000})
        d += _dt.timedelta(days=1)
    return rows


_SERIES = _series(400)


def _get_bars(symbol, timespan="D", count="0", **_k):
    return list(_SERIES)


def _strategy(name):
    return Strategy(name=name, symbol="SPY",
                    entry=SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))


def _rec(rec_id, *, trial_id=None, gate_a=None, archetype="trend_follow"):
    return StrategyRecord(id=rec_id, strategy=_strategy(rec_id), fingerprint=rec_id,
                          canon_bucket="cb-" + rec_id, archetype=archetype,
                          cohort=archetype + ":up/low", status="proving",
                          created_at_iso="2022-01-01", as_of="2022-01-01",
                          trial_id=trial_id, gate_a=gate_a)


def _seed():
    return SeedEntry(fingerprint="seed", strategy=_strategy("seed"), archetype="trend_follow",
                     why="", status="confirmed_proven")


def _bt():
    return BacktestResult(total_return_pct=0.0, buy_hold_return_pct=0.0, num_trades=0, win_rate=0.0,
                          avg_win_pct=0.0, avg_loss_pct=0.0, expectancy=0.0, max_drawdown_pct=0.0,
                          equity_curve=[], trades=[])


def _book(n):
    curve = [EquityPoint(time=f"2022-01-{i + 1:02d}", equity=10000 + i * 10) for i in range(n)]
    return TrialBook(scope="single", starting_equity=10000.0, metrics=_bt(), equity_curve=curve)


# ── _by_arch / _pass_rate ─────────────────────────────────────────────────────
def test_by_arch_counts_accepted_records_by_archetype():
    lib = [_rec("fa", archetype="trend_follow"), _rec("fb", archetype="momentum"),
           _rec("fc", archetype="trend_follow")]
    assert cycle._by_arch(["fa", "fc"], lib) == {"trend_follow": 2}
    assert cycle._by_arch(["fb"], lib) == {"momentum": 1}


def test_pass_rate_none_when_nothing_screened():
    assert cycle._pass_rate(IngestResult(accepted=[], gate_a_failed=[], m_after=0)) is None


def test_pass_rate_is_accepted_over_screened():
    ing = IngestResult(accepted=["a", "b", "c"],
                       gate_a_failed=[{"fingerprint": "x", "fail_codes": []}], m_after=4)
    assert cycle._pass_rate(ing) == 0.75


# ── _assign_cohorts ───────────────────────────────────────────────────────────
def test_assign_cohorts_only_with_enough_curve_points():
    big, small, none = _rec("big", trial_id="t-big"), _rec("small", trial_id="t-small"), _rec("none")
    lib = [big, small, none]
    books = {"t-big": _book(12), "t-small": _book(5)}        # cohort_min_curve_points == 10
    cycle._assign_cohorts(lib, books, DEFAULT_LAB_CONFIG)
    assert big.behavioral_cohort == dedup.behavioral_cohort(books["t-big"].equity_curve)
    assert small.behavioral_cohort is None and none.behavioral_cohort is None


# ── _open_admitted ────────────────────────────────────────────────────────────
def test_open_admitted_opens_up_to_cap_highest_gate_a_score_first():
    lib = [_rec("lo", gate_a=WalkForwardReport(passed=True, dsr=0.10)),
           _rec("mid", gate_a=WalkForwardReport(passed=True, dsr=0.50)),
           _rec("hi", gate_a=WalkForwardReport(passed=True, dsr=0.99))]
    saved = []
    opened = cycle._open_admitted(lib, get_bars=_get_bars, save_state=lambda st: saved.append(st.trial_id),
                                  gate_a_cfg=DEFAULT_GATE_A_CONFIG, today_et="2022-06-01",
                                  now_iso="2022-06-01T00:00:00+00:00", m=4, cap=2)
    assert opened == ["hi", "mid"] and saved == ["hi", "mid"]            # highest dsr first, capped
    assert lib[2].trial_id == "hi" and lib[1].trial_id == "mid" and lib[0].trial_id is None


# ── decide_batch ──────────────────────────────────────────────────────────────
def test_decide_batch_cold_start_is_all_explore():
    d = cycle.decide_batch([], seeds=[], reseed_result=ReseedResult(seeds=[], explore_quota=2),
                           stagnation=StagnationState(), cfg=DEFAULT_LAB_CONFIG)
    assert d.cold_start is True and d.open_slots == 25 and d.pass_rate_est == 0.25
    assert d.mutation_n == 0 and d.explore_n == 64          # min(64, ceil(25/0.25)==100)
    assert d.max_admit == 25 and d.cohort_caps == {}


def test_decide_batch_pass_rate_floored():
    d = cycle.decide_batch([], seeds=[], reseed_result=ReseedResult(explore_quota=1),
                           stagnation=StagnationState(pass_rate_ewma=0.01), cfg=DEFAULT_LAB_CONFIG)
    assert d.pass_rate_est == 0.05                          # max(pass_rate_floor, ewma)


def test_decide_batch_steady_state_oversamples_to_fill_slots():
    lib = [_rec(f"r{i}", trial_id=f"t{i}") for i in range(5)]            # active 5
    d = cycle.decide_batch(lib, seeds=[_seed()],
                           reseed_result=ReseedResult(seeds=[_seed()], explore_quota=2),
                           stagnation=StagnationState(pass_rate_ewma=0.5, throttle={"trend_follow": 1.0}),
                           cfg=DEFAULT_LAB_CONFIG)
    assert d.cold_start is False and d.active_trials == 5 and d.open_slots == 20
    assert d.pass_rate_est == 0.5 and d.explore_n == 2      # round(2*(2-1.0))
    assert d.mutation_n == math.ceil(20 / 0.5)             # 40, < (max_candidates 64 - explore_n 2)
    assert d.mutation_n == 40 and d.cohort_caps == {"none": 12}


def test_decide_batch_waiting_backlog_reduces_open_slots():
    lib = [_rec(f"a{i}", trial_id=f"t{i}") for i in range(3)] + [_rec(f"w{i}") for i in range(2)]
    d = cycle.decide_batch(lib, seeds=[_seed()],
                           reseed_result=ReseedResult(seeds=[_seed()], explore_quota=1),
                           stagnation=StagnationState(pass_rate_ewma=0.25, throttle={"trend_follow": 1.0}),
                           cfg=DEFAULT_LAB_CONFIG)
    assert d.active_trials == 3 and d.admitted_waiting == 2
    assert d.open_slots == 20 and d.max_admit == 20


def test_decide_batch_full_pipe_generates_nothing():
    lib = [_rec(f"r{i}", trial_id=f"t{i}") for i in range(25)]
    d = cycle.decide_batch(lib, seeds=[_seed()],
                           reseed_result=ReseedResult(seeds=[_seed()], explore_quota=5),
                           stagnation=StagnationState(throttle={"trend_follow": 1.0}),
                           cfg=DEFAULT_LAB_CONFIG)
    assert d.open_slots == 0 and d.mutation_n == 0 and d.explore_n == 0 and d.max_admit == 0


def test_decide_batch_explore_boosts_as_throttle_falls():
    lib = [_rec(f"r{i}", trial_id=f"t{i}") for i in range(5)]
    rr = ReseedResult(seeds=[_seed()], explore_quota=4)
    healthy = cycle.decide_batch(lib, seeds=[_seed()], reseed_result=rr,
                                 stagnation=StagnationState(pass_rate_ewma=0.5, throttle={"a": 1.0, "b": 1.0}),
                                 cfg=DEFAULT_LAB_CONFIG)
    stagnating = cycle.decide_batch(lib, seeds=[_seed()], reseed_result=rr,
                                    stagnation=StagnationState(pass_rate_ewma=0.5, throttle={"a": 0.25, "b": 0.25}),
                                    cfg=DEFAULT_LAB_CONFIG)
    assert healthy.explore_n == 4 and stagnating.explore_n == 7          # round(4*1.0) vs round(4*1.75)
    assert stagnating.explore_n > healthy.explore_n


# ── run_cycle ─────────────────────────────────────────────────────────────────
import random

import webull_api.lab.gate_a as gate_a_mod
import webull_api.lab.trial as trial_mod
import webull_api.lab.verdict as verdict_mod
from webull_api.lab.schema import LabConfig, ProvingState
from webull_api.strategy.cost import NO_COST


def _pass_screen(monkeypatch):
    """All candidates pass Gate A (mirrors test_lab_orchestrator) so the cycle's composition — not
    the statistical gate — is under test. dsr/pooled feed _open_admitted ordering."""
    def fake(strategy, bars_by_symbol, *, cfg, cost, m, require_cost_robust=False,
             lockbox_bars_by_symbol=None):
        return WalkForwardReport(passed=True, fail_codes=[], regime_buckets=["up/low"],
                                 m_at_eval=m, dsr=0.96, pooled_expectancy=0.5)
    monkeypatch.setattr(gate_a_mod, "screen_one", fake)


def _state(tid):
    return ProvingState(trial_id=tid, strategy=_strategy(tid), basket=["SPY"],
                        inception_et_date="2022-01-01", started_at_iso="2022-01-01")


def test_run_cycle_admits_opens_trials_and_keeps_m_consistent(monkeypatch):
    _pass_screen(monkeypatch)
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 1))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    cfg = LabConfig(max_concurrent_proving=3)
    states = {}
    lib = []
    new_lib, report = cycle.run_cycle(
        lib, cfg=cfg, cost=NO_COST, get_bars=_get_bars,
        load_state=lambda tid: states[tid], save_state=lambda st: states.__setitem__(st.trial_id, st),
        load_books=lambda: {}, today_et="2022-06-01", now_iso="2022-06-01T00:00:00+00:00",
        m=0, tombstones=[], stagnation=StagnationState(), rng=random.Random(7))

    assert new_lib is lib                                          # mutated in place
    assert report.decision.cold_start is True
    assert len(report.accepted) >= 1
    assert report.screened == report.m_after - report.m_before    # M moves only via screened
    assert report.screened == len(report.accepted) + len(report.gate_a_failed)
    assert 1 <= len(report.new_proving) <= len(report.accepted) <= cfg.max_concurrent_proving
    assert report.m_after >= len(new_lib)                         # M is an UPPER bound on #records
    for fp in report.accepted:
        rec = next(r for r in new_lib if r.id == fp)
        assert rec.status == "proving" and rec.trial_id == rec.id
    assert report.stagnation.cycles_run == 1 and report.cycle_seq == 0   # shell overwrites cycle_seq


def test_run_cycle_assigns_behavioral_cohorts_from_books(monkeypatch):
    _pass_screen(monkeypatch)
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 1))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    seeded = _rec("seeded", trial_id="t-seeded", gate_a=WalkForwardReport(passed=True, dsr=0.96))
    lib = [seeded]
    states = {"t-seeded": _state("t-seeded")}
    books = {"t-seeded": _book(12)}
    cycle.run_cycle(lib, cfg=LabConfig(max_concurrent_proving=3), cost=NO_COST, get_bars=_get_bars,
                    load_state=lambda tid: states[tid],
                    save_state=lambda st: states.__setitem__(st.trial_id, st),
                    load_books=lambda: books, today_et="2022-06-01", now_iso="2022-06-01T00:00:00+00:00",
                    m=0, tombstones=[], stagnation=StagnationState(), rng=random.Random(3))
    assert seeded.behavioral_cohort == dedup.behavioral_cohort(books["t-seeded"].equity_curve)


def test_run_cycle_full_pipe_is_advance_only_heartbeat(monkeypatch):
    _pass_screen(monkeypatch)
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 0))   # no new bar
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    rec = _rec("only", trial_id="t-only", gate_a=WalkForwardReport(passed=True, dsr=0.9))
    states = {"t-only": _state("t-only")}
    _, report = cycle.run_cycle(
        [rec], cfg=LabConfig(max_concurrent_proving=1), cost=NO_COST, get_bars=_get_bars,
        load_state=lambda tid: states[tid], save_state=lambda st: None, load_books=lambda: {},
        today_et="2022-06-02", now_iso="2022-06-02T00:00:00+00:00", m=9, tombstones=[],
        stagnation=StagnationState(), rng=random.Random(1))
    assert report.no_op is True and report.no_bar is True
    assert report.decision.open_slots == 0
    assert report.generated == 0 and report.accepted == [] and report.new_proving == []
    assert report.m_after == 9 and report.m_before == 9          # zero M spend on a full pipe


def test_run_cycle_is_deterministic_for_same_inputs(monkeypatch):
    _pass_screen(monkeypatch)
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 1))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    cfg = LabConfig(max_concurrent_proving=3)

    def _run():
        states, lib = {}, []
        return cycle.run_cycle(lib, cfg=cfg, cost=NO_COST, get_bars=_get_bars,
                               load_state=lambda tid: states[tid],
                               save_state=lambda st: states.__setitem__(st.trial_id, st),
                               load_books=lambda: {}, today_et="2022-06-01",
                               now_iso="2022-06-01T00:00:00+00:00", m=0, tombstones=[],
                               stagnation=StagnationState(), rng=random.Random(5))
    lib_a, a = _run()
    lib_b, b = _run()
    assert a.accepted == b.accepted and a.m_after == b.m_after and a.new_proving == b.new_proving
    assert sorted(r.fingerprint for r in lib_a) == sorted(r.fingerprint for r in lib_b)


def test_run_cycle_rerun_on_full_pipe_spends_no_extra_m(monkeypatch):
    """Crash-resume proof: once the pipe is full, re-entering the cycle adds no M and opens no
    double trials (dedup + open_slots==0). Models a same-(date) re-trigger against a persisted lib."""
    _pass_screen(monkeypatch)
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 1))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    cfg = LabConfig(max_concurrent_proving=2)
    states, lib = {}, []
    common = dict(cfg=cfg, cost=NO_COST, get_bars=_get_bars,
                  load_state=lambda tid: states[tid],
                  save_state=lambda st: states.__setitem__(st.trial_id, st),
                  load_books=lambda: {}, today_et="2022-06-01", now_iso="2022-06-01T00:00:00+00:00",
                  tombstones=[])
    _, a = cycle.run_cycle(lib, m=0, stagnation=StagnationState(), rng=random.Random(11), **common)
    assert len(a.new_proving) == 2 and a.m_after >= 2             # pipe filled
    _, b = cycle.run_cycle(lib, m=a.m_after, stagnation=a.stagnation, rng=random.Random(11), **common)
    assert b.decision.open_slots == 0
    assert b.accepted == [] and b.new_proving == [] and b.m_after == a.m_after   # no double-spend
