import pytest

from webull_api.lab.schema import StagnationState
from webull_api.lab.stagnation import update_stagnation


def test_dry_streak_increments_on_zero_accept():
    s = StagnationState(overall_dry_streak=1)
    out = update_stagnation(s, accepted_by_archetype={}, total_accepted=0,
                            observed_pass_rate=0.0, archetypes_present=set(), cycle_date="d")
    assert out.overall_dry_streak == 2


def test_dry_streak_resets_on_accept():
    s = StagnationState(overall_dry_streak=3)
    out = update_stagnation(s, accepted_by_archetype={"a": 1}, total_accepted=2,
                            observed_pass_rate=0.5, archetypes_present={"a"}, cycle_date="d")
    assert out.overall_dry_streak == 0


def test_throttle_decays_after_patience_and_does_not_mutate_prev():
    prev = StagnationState(per_archetype_dry={"a": 2}, throttle={"a": 1.0})
    out = update_stagnation(prev, accepted_by_archetype={"a": 0}, total_accepted=0,
                            observed_pass_rate=0.2, archetypes_present={"a"}, cycle_date="2026-01-02")
    assert out.per_archetype_dry["a"] == 3      # 2 + 1, hits patience=3
    assert out.throttle["a"] == 0.5             # 1.0 * throttle_decay
    assert prev.throttle == {"a": 1.0}          # prev untouched (new state returned)
    out2 = update_stagnation(out, accepted_by_archetype={"a": 0}, total_accepted=0,
                             observed_pass_rate=None, archetypes_present={"a"}, cycle_date="2026-01-03")
    assert out2.per_archetype_dry["a"] == 4 and out2.throttle["a"] == 0.25   # geometric decay


def test_throttle_recovers_on_fresh_passer():
    prev = StagnationState(per_archetype_dry={"a": 4}, throttle={"a": 0.25}, overall_dry_streak=4)
    out = update_stagnation(prev, accepted_by_archetype={"a": 2}, total_accepted=2,
                            observed_pass_rate=0.4, archetypes_present={"a"}, cycle_date="2026-01-05")
    assert out.throttle["a"] == 1.0 and out.per_archetype_dry["a"] == 0
    assert out.overall_dry_streak == 0


def test_per_archetype_isolation():
    prev = StagnationState(per_archetype_dry={"a": 2, "b": 2}, throttle={"a": 1.0, "b": 1.0})
    out = update_stagnation(prev, accepted_by_archetype={"a": 1, "b": 0}, total_accepted=1,
                            observed_pass_rate=0.3, archetypes_present={"a", "b"},
                            cycle_date="2026-01-06")
    assert out.throttle["a"] == 1.0 and out.per_archetype_dry["a"] == 0    # a recovers
    assert out.throttle["b"] == 0.5 and out.per_archetype_dry["b"] == 3    # b throttles


def test_carry_unchanged_for_absent_archetypes():
    prev = StagnationState(per_archetype_dry={"c": 5}, throttle={"c": 0.5})
    out = update_stagnation(prev, accepted_by_archetype={}, total_accepted=0,
                            observed_pass_rate=0.2, archetypes_present={"a"}, cycle_date="2026-01-07")
    assert out.throttle["c"] == 0.5 and out.per_archetype_dry["c"] == 5    # absent -> untouched
    assert out.per_archetype_dry["a"] == 1                                 # present -> tracked


def test_ewma_seeds_then_blends():
    s1 = update_stagnation(StagnationState(), accepted_by_archetype={"a": 1}, total_accepted=1,
                           observed_pass_rate=0.4, archetypes_present={"a"}, cycle_date="d1")
    assert s1.pass_rate_ewma == 0.4                                        # seeded (prev ewma == 0)
    s2 = update_stagnation(s1, accepted_by_archetype={"a": 1}, total_accepted=1,
                           observed_pass_rate=0.2, archetypes_present={"a"}, cycle_date="d2")
    assert s2.pass_rate_ewma == pytest.approx(0.3 * 0.2 + 0.7 * 0.4)       # alpha-blend == 0.34


def test_ewma_carries_when_observed_none():
    s = StagnationState(pass_rate_ewma=0.4)
    out = update_stagnation(s, accepted_by_archetype={}, total_accepted=0, observed_pass_rate=None,
                            archetypes_present=set(), cycle_date="d")
    assert out.pass_rate_ewma == 0.4


def test_cycles_run_increments_and_records_date():
    out = update_stagnation(StagnationState(cycles_run=5), accepted_by_archetype={}, total_accepted=0,
                            observed_pass_rate=0.1, archetypes_present=set(), cycle_date="2026-02-02")
    assert out.cycles_run == 6 and out.last_cycle_date == "2026-02-02" and out.overall_dry_streak == 1
