from webull_api.lab.reseed import (archetype_quota, cooldown_active, explore_quota,
                                    lineage_penalty, next_cooldown, select_seeds)
from webull_api.lab.schema import (DEFAULT_LAB_CONFIG, ProposalRecord,
                                   RegimeTag, StrategyRecord)
from webull_api.strategy.schema import SmaCross, Strategy


def _rec(rec_id, *, status="proving", archetype="trend_follow", cooldown=None, generation=0,
         behavioral_cohort=None):
    strat = Strategy(name=rec_id, symbol="AAPL",
                     entry=SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))
    return StrategyRecord(
        id=rec_id, strategy=strat, fingerprint=rec_id, canon_bucket=rec_id,
        behavioral_cohort=behavioral_cohort, archetype=archetype, generation=generation,
        cohort=f"{archetype}:up/low", status=status, created_at_iso="2026-06-01",
        as_of="2026-06-01", cooldown_until_iso=cooldown,
        lineage=ProposalRecord(generation=generation))


def test_next_cooldown_escalates_then_retires():
    assert next_cooldown(1) == 60          # first kill -> 60d
    assert next_cooldown(2) == 180         # second kill -> 180d
    assert next_cooldown(3) is None        # third kill -> retire (None)


def test_cooldown_active_true_then_false_after_window():
    rec = _rec("x", status="rejected", cooldown="2026-12-31T00:00:00")
    assert cooldown_active(rec, "2026-06-29T00:00:00") is True
    assert cooldown_active(rec, "2027-01-01T00:00:00") is False


def test_cooldown_active_false_when_none():
    assert cooldown_active(_rec("x"), "2026-06-29T00:00:00") is False


def test_explore_quota_decays_with_saturation():
    full = explore_quota(0.0)      # round(8 * 0.30) = 2
    sat = explore_quota(1.0)       # round(8 * 0.10) = 1
    assert full == 2 and sat == 1 and full >= sat


def test_lineage_penalty_grows_with_generation():
    assert lineage_penalty(_rec("g0", generation=0)) == 0.0
    assert abs(lineage_penalty(_rec("g3", generation=3)) - 0.30) < 1e-9


def test_archetype_quota_caps_per_archetype():
    lib = [_rec("a", archetype="trend_follow"), _rec("b", archetype="breakout")]
    q = archetype_quota(lib)
    cap = int(DEFAULT_LAB_CONFIG.seed_quota * DEFAULT_LAB_CONFIG.max_archetype_frac)
    assert q == {"breakout": cap, "trend_follow": cap}


def _rn():
    return RegimeTag(trend="up", vol="low")


def test_select_seeds_only_from_gate_b_survivors():
    lib = [
        _rec("p1", status="proving"),            # Gate-A-only -> NEVER a seed
        _rec("ga_failed", status="gate_a_failed"),
        _rec("killed", status="rejected"),
        _rec("prov1", status="provisional_proven"),
        _rec("conf1", status="confirmed_proven"),
    ]
    res = select_seeds(lib, "2026-06-29T00:00:00", regime_now=_rn())
    fps = {s.fingerprint for s in res.seeds}
    assert fps == {"prov1", "conf1"}
    assert "p1" not in fps and "killed" not in fps and "ga_failed" not in fps


def test_select_seeds_confirmed_outranks_provisional():
    lib = [_rec("prov", status="provisional_proven"), _rec("conf", status="confirmed_proven")]
    res = select_seeds(lib, "2026-06-29T00:00:00", regime_now=_rn())
    assert res.seeds[0].fingerprint == "conf"


def test_select_seeds_archetype_cap():
    # 6 confirmed of one archetype; cap = 8 * 0.5 = 4
    lib = [_rec(f"c{i}", status="confirmed_proven", archetype="trend_follow") for i in range(6)]
    res = select_seeds(lib, "2026-06-29T00:00:00", regime_now=_rn())
    assert len(res.seeds) == 4


def test_select_seeds_excludes_cooldown_active_survivor():
    lib = [_rec("conf", status="confirmed_proven", cooldown="2027-01-01T00:00:00")]
    res = select_seeds(lib, "2026-06-29T00:00:00", regime_now=_rn())
    assert res.seeds == []


def test_select_seeds_reports_explore_quota():
    lib = [_rec("conf", status="confirmed_proven")]
    res = select_seeds(lib, "2026-06-29T00:00:00", regime_now=_rn())
    assert res.explore_quota >= 1
