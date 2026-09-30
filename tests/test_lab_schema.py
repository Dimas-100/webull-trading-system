import dataclasses
import pytest
from webull_api.strategy.schema import Strategy
from webull_api.lab import schema as ls
from webull_api.lab.schema import LAB_BASKET


def _strat():
    return Strategy(name="s", symbol="SPY",
                    entry={"type": "sma_cross", "fast": 10, "slow": 20, "direction": "above"})


def test_default_config_singletons_and_values():
    assert ls.DEFAULT_GATE_A_CONFIG.min_lookback_bars == 750
    assert ls.DEFAULT_GATE_A_CONFIG.dsr_min == 0.95
    assert ls.DEFAULT_GATE_A_CONFIG.min_total_trades == 100
    assert ls.DEFAULT_GATE_B_CONFIG.min_forward_bars == 126
    assert ls.DEFAULT_GATE_B_CONFIG.kill_drawdown_pct == -20.0
    assert ls.DEFAULT_LAB_CONFIG.max_concurrent_proving == 25
    assert ls.DEFAULT_SCORE_WEIGHTS.proving_cap == 70.0
    assert ls.DEFAULT_COST_CONFIG.slippage_pct == 0.05


def test_config_dataclasses_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        ls.DEFAULT_GATE_A_CONFIG.dsr_min = 0.1


def test_lab_basket_is_sector_diverse_tuple():
    assert isinstance(ls.LAB_BASKET, tuple)
    assert ls.LAB_BASKET[0] == "SPY"
    assert len(ls.LAB_BASKET) == 23
    assert len(set(ls.LAB_BASKET)) == 23      # no dupes
    assert ls.OBJECTIVE_TEXT.strip() != ""


def test_regime_tag_bucket():
    assert ls.RegimeTag(trend="up", vol="high").bucket() == "up/high"


def test_walk_forward_report_defaults_round_trip():
    r = ls.WalkForwardReport()
    again = ls.WalkForwardReport.model_validate(r.model_dump())
    assert again.passed is False and again.dsr == 0.0 and again.num_leaves == 1
    assert again.expectancy_ci == (0.0, 0.0)


def test_strategy_record_minimal_round_trip():
    rec = ls.StrategyRecord(id="rec1", strategy=_strat(), fingerprint="fp", canon_bucket="cb",
                            archetype="trend_follow", cohort="trend_follow:up/low",
                            created_at_iso="2026-06-29T00:00:00", as_of="2026-06-29")
    again = ls.StrategyRecord.model_validate(rec.model_dump())
    assert again.status == "proposed" and again.origin == "lab_generated"
    assert again.kills == 0 and again.trial_summary is None


def test_proving_state_round_trip():
    st = ls.ProvingState(trial_id="t1", strategy=_strat(), basket=list(ls.LAB_BASKET),
                         inception_et_date="2026-06-29", started_at_iso="2026-06-29T00:00:00")
    again = ls.ProvingState.model_validate(st.model_dump())
    assert again.status == "proving" and again.book is None and again.bars_observed == 0


def test_paper_proven_record_preinstalls_the_human_gate():
    rec = ls.PaperProvenRecord(
        graduated_at="2026-06-29T00:00:00", strategy=_strat(),
        gate_a=ls.WalkForwardReport(), gate_b={"trial_id": "t1"},
        assumptions={"note": "forward edge is an UPPER BOUND"},
        objective_met={"profitable_window_frac": 0.7})
    assert rec.verdict == "confirmed_proven"
    assert rec.promotion is None and rec.capital_allocation is None
    assert rec.live_authorization is None and rec.human_promotion_authorized is False


def test_stagnation_state_defaults_and_empty_validate():
    s = ls.StagnationState()
    assert s.overall_dry_streak == 0 and s.pass_rate_ewma == 0.0 and s.cycles_run == 0
    assert s.per_archetype_dry == {} and s.throttle == {} and s.last_cycle_date == ""
    # a fresh/empty persisted blob (meta['stagnation'] == {}) validates to a clean state
    again = ls.StagnationState.model_validate({})
    assert again.overall_dry_streak == 0 and again.schema_version == 1


def test_batch_decision_round_trip():
    d = ls.BatchDecision(active_trials=2, admitted_waiting=1, open_slots=3, pass_rate_est=0.25,
                         mutation_n=12, explore_n=4, max_admit=3)
    again = ls.BatchDecision.model_validate(d.model_dump())
    assert again.open_slots == 3 and again.cold_start is False and again.cohort_caps == {}


def test_cycle_report_round_trip_and_one_line():
    rep = ls.CycleReport(
        cycle_seq=7, cycle_date="2026-06-30", ran_at_iso="2026-06-30T13:00:00",
        regime=ls.RegimeTag(trend="up", vol="high"),
        decision=ls.BatchDecision(active_trials=0, admitted_waiting=0, open_slots=5,
                                  pass_rate_est=0.25, mutation_n=0, explore_n=20, max_admit=5),
        m_before=10, m_after=14, stagnation=ls.StagnationState(),
        generated=20, accepted=["fp1", "fp2"], new_proving=["fp1"],
        advanced=["a"], promoted=[], killed=[], proving_active=1, slots_cap=25,
        throttled_archetypes=["mean_revert"])
    again = ls.CycleReport.model_validate(rep.model_dump())
    assert again.cycle_seq == 7 and again.m_after == 14 and again.no_op is False
    line = rep.one_line()
    assert isinstance(line, str)
    assert "cycle #7" in line and "2026-06-30" in line
    assert "M 10→14" in line and "up/high" in line
    assert "throttled mean_revert" in line


def test_new_lab_config_fields_defaults():
    c = ls.DEFAULT_LAB_CONFIG
    assert c.stagnation_patience == 3 and c.throttle_decay == 0.5
    assert c.throttle_floor == 0.0 and c.throttle_recover == 1.0
    assert c.pass_rate_default == 0.25 and c.pass_rate_alpha == 0.3
    assert c.pass_rate_floor == 0.05 and c.cohort_min_curve_points == 10


def test_proposal_brief_avoid_canon_buckets_back_compat():
    # an OLD persisted brief WITHOUT the new field still loads; new field defaults empty
    old = {"as_of": "2026-06-30", "regime_now": {"trend": "up", "vol": "low"},
           "universe": ["SPY"], "objective": "x"}
    brief = ls.ProposalBrief.model_validate(old)
    assert brief.avoid_canon_buckets == []
    brief2 = ls.ProposalBrief(as_of="2026-06-30", regime_now=ls.RegimeTag(trend="up", vol="low"),
                              universe=["SPY"], objective="x", avoid_canon_buckets=["a/b|c"])
    assert brief2.avoid_canon_buckets == ["a/b|c"]


def test_lab_basket_widened_to_23():
    assert len(LAB_BASKET) == 23 and len(set(LAB_BASKET)) == 23
    for sym in ("LLY", "COST", "IWM", "TSLA", "AMD"):
        assert sym in LAB_BASKET
    assert "GOOG" not in LAB_BASKET and "VOO" not in LAB_BASKET   # GOOGL covers; VOO ≈ SPY
