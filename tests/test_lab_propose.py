import random

import pytest

from webull_api.lab import archetype, propose
from webull_api.lab.propose import (
    DEFAULT_PROPOSE_CONFIG,
    EVENT_LEAVES,
    LEAF_PARAM_GRID,
    SIBLING_GROUPS,
    STATE_LEAVES,
    _make_leaf,
    structural_cohort,
)
from webull_api.lab.schema import DEFAULT_LAB_CONFIG, LAB_BASKET
from webull_api.strategy.schema import (
    AllOf,
    RsiCond,
    SmaCross,
    Strategy,
    leaf_count,
)


def _strat(entry, **kw):
    return Strategy(name=kw.pop("name", "s"), symbol=kw.pop("symbol", "SPY"),
                    origin="lab_generated", entry=entry, **kw)


def test_constants_shapes():
    assert EVENT_LEAVES == ("sma_cross", "ema_cross", "breakout", "rsi", "macd",
                            "ibs", "zscore", "drop_from_high", "consec_down")
    assert STATE_LEAVES == ("price_vs_sma", "atr_pct")
    assert SIBLING_GROUPS["trend_follow"] == ("sma_cross", "ema_cross", "price_vs_sma", "atr_pct")
    assert SIBLING_GROUPS["momentum"] == ("rsi_above", "macd", "ibs_above", "zscore_above")
    assert SIBLING_GROUPS["mean_revert"] == ("rsi_below", "ibs_below", "zscore_below",
                                             "drop_from_high", "consec_down")
    assert SIBLING_GROUPS["breakout"] == ("breakout",)
    # every EVENT/STATE leaf type has a grid entry
    for t in EVENT_LEAVES + STATE_LEAVES:
        assert t in LEAF_PARAM_GRID


def test_propose_config_defaults():
    cfg = DEFAULT_PROPOSE_CONFIG
    assert cfg.lab is DEFAULT_LAB_CONFIG
    assert cfg.universe == LAB_BASKET
    assert cfg.default_symbol == LAB_BASKET[0] == "SPY"
    assert cfg.timeframe == "1D"
    assert cfg.bucket_step == cfg.lab.canon_period_bucket == 5
    assert cfg.max_attempts_per_slot == 8
    assert cfg.stop_grid == (3, 4, 5, 6, 8, 10)
    assert cfg.target_grid == (6, 8, 10, 12, 15, 20)
    assert cfg.archetype_weights is None and cfg.leaf_type_weights is None


def test_structural_cohort_signature_format():
    entry = AllOf(type="all_of", conditions=[
        SmaCross(type="sma_cross", fast=10, slow=50, direction="above"),
        RsiCond(type="rsi", period=14, threshold=30, comparison="below"),
    ])
    s = _strat(entry)
    coh = structural_cohort(s)
    # classify|sorted leaf-type multiset|filt|exit
    assert coh == f"{archetype.classify(s)}|rsi+sma_cross|filt:False|exit:False"


def test_make_leaf_every_token_builds_a_valid_strategy():
    rng = random.Random(0)
    tokens = ["sma_cross", "ema_cross", "rsi_above", "rsi_below", "breakout",
              "price_vs_sma", "macd", "atr_pct"]
    for _ in range(200):
        for tok in tokens:
            leaf = _make_leaf(tok, rng)
            s = _strat(leaf)  # model_validate via constructor path
            assert leaf_count(s.entry) == 1
            if tok == "rsi_above":
                assert leaf["type"] == "rsi" and leaf["comparison"] == "above"
            elif tok == "rsi_below":
                assert leaf["type"] == "rsi" and leaf["comparison"] == "below"
            elif tok != "macd":
                assert leaf["type"] == tok


from webull_api.lab.propose import explore


def test_explore_1000_all_valid_in_bounds_event_entry_state_filter():
    rng = random.Random(0)
    filters_seen = 0
    composites_seen = 0
    for _ in range(1000):
        s = explore(rng)
        # valid by construction (explore returns a model_validate'd Strategy or raises)
        assert isinstance(s, Strategy)
        assert s.origin == "lab_generated"
        assert s.symbol == DEFAULT_PROPOSE_CONFIG.default_symbol
        assert s.timeframe == "1D"
        total = leaf_count(s.entry) + leaf_count(s.exit) + leaf_count(s.filter)
        assert total <= 6
        # entry leaves are all EVENT leaves
        entry_leaves = archetype._leaves(s.entry)
        assert 1 <= len(entry_leaves) <= 2
        if len(entry_leaves) == 2:
            composites_seen += 1
        for leaf in entry_leaves:
            assert leaf.type in EVENT_LEAVES
        # filter, if present, is a single STATE leaf
        if s.filter is not None:
            assert leaf_count(s.filter) == 1
            assert s.filter.type in STATE_LEAVES
            filters_seen += 1
    assert filters_seen > 0          # p_add_filter=0.5 over 1000
    assert composites_seen > 0       # p_two_leaf_entry=0.35 over 1000


def test_explore_is_deterministic_for_a_given_seed():
    a = [explore(random.Random(42)).model_dump() for _ in range(5)]
    b = [explore(random.Random(42)).model_dump() for _ in range(5)]
    assert a == b


# ──────────────────────────── Task 6 — mutate ────────────────────────────
from webull_api.lab.dedup import canon_bucket, fingerprint
from webull_api.lab.propose import mutate
from webull_api.strategy.schema import EmaCross, PriceVsSma


def _seed_single():
    return _strat(SmaCross(type="sma_cross", fast=10, slow=50, direction="above"),
                  stop_loss_pct=5, take_profit_pct=10)


def _seed_composite():
    return _strat(AllOf(type="all_of", conditions=[
        EmaCross(type="ema_cross", fast=10, slow=50, direction="above"),
        RsiCond(type="rsi", period=14, threshold=70, comparison="above"),
    ]), filter=PriceVsSma(type="price_vs_sma", period=200, side="above"),
        stop_loss_pct=6, take_profit_pct=12)


def test_mutate_returns_valid_fingerprint_distinct_strategy():
    for seed in (_seed_single(), _seed_composite()):
        base_fp = fingerprint(seed)
        for n in range(200):
            out = mutate(seed, random.Random(n))
            assert isinstance(out, Strategy)                 # valid by model_validate
            assert fingerprint(out) != base_fp               # distinct rule
            total = leaf_count(out.entry) + leaf_count(out.exit) + leaf_count(out.filter)
            assert total <= 6                                # leaf budget preserved
            # depth-1 preserved: a composite's conditions are all non-composite leaves
            if out.entry.type in ("all_of", "any_of"):
                for c in out.entry.conditions:
                    assert c.type not in ("all_of", "any_of")


def test_mutate_preserves_fast_lt_slow_and_no_dup_leaves():
    seed = _seed_composite()
    for n in range(300):
        out = mutate(seed, random.Random(n))
        for slot in (out.entry, out.exit, out.filter):
            if slot is None:
                continue
            leaves = slot.conditions if slot.type in ("all_of", "any_of") else [slot]
            for leaf in leaves:
                if leaf.type in ("sma_cross", "ema_cross", "macd"):
                    assert leaf.fast < leaf.slow
            dumps = [leaf.model_dump() for leaf in leaves]
            assert len(dumps) == len({tuple(sorted(d.items())) for d in dumps})  # no dup leaves


def test_mutate_is_deterministic_for_a_given_seed():
    seed = _seed_single()
    assert mutate(seed, random.Random(7)).model_dump() == mutate(seed, random.Random(7)).model_dump()


def test_mutate_never_raises_on_a_degenerate_seed():
    # minimal seed with no int-bumpable headroom is still mutated (fallback = adjust stops)
    seed = _strat(RsiCond(type="rsi", period=14, threshold=30, comparison="below"))
    out = mutate(seed, random.Random(1))
    assert isinstance(out, Strategy)
    assert fingerprint(out) != fingerprint(seed)


# ──────────────────────────── Task 7 — bias_config ────────────────────────────
from webull_api.lab.propose import bias_config
from webull_api.lab.schema import ArchetypeAggregate, ProposalBrief, RegimeTag, ShapedFeedback


def _brief(most_failing=None, vol="low", archetype_stats=None):
    return ProposalBrief(
        as_of="2026-01-01",
        regime_now=RegimeTag(trend="up", vol=vol),
        universe=list(LAB_BASKET),
        objective="x",
        archetype_stats=archetype_stats or [],
        feedback=ShapedFeedback(most_failing_condition=most_failing),
    )


def test_bias_archetype_weight_from_kill_rate():
    stats = [ArchetypeAggregate(archetype="breakout", lifetime_proposed=10,
                                lifetime_proving=0, lifetime_proven=0, lifetime_killed=8)]
    cfg = bias_config(_brief(archetype_stats=stats))
    # weight = max(floor=0.15, 1 - 0.8) == 0.2
    assert cfg.archetype_weights["breakout"] == pytest.approx(0.2)


def test_bias_archetype_weight_floors_at_config_floor():
    stats = [ArchetypeAggregate(archetype="momentum", lifetime_proposed=10,
                                lifetime_proving=0, lifetime_proven=0, lifetime_killed=10)]
    cfg = bias_config(_brief(archetype_stats=stats))
    assert cfg.archetype_weights["momentum"] == pytest.approx(DEFAULT_PROPOSE_CONFIG.archetype_floor_weight)


def test_bias_high_vol_nudges_filter_prob():
    cfg = bias_config(_brief(vol="high"))
    assert cfg.p_add_filter == pytest.approx(min(1.0, DEFAULT_PROPOSE_CONFIG.p_add_filter + 0.2))
    low = bias_config(_brief(vol="low"))
    assert low.p_add_filter == pytest.approx(DEFAULT_PROPOSE_CONFIG.p_add_filter)


def test_bias_most_failing_condition_lowers_that_leaf_rate_at_fixed_seed():
    def _rsi_entry_count(cfg, n=400, seed=0):
        rng = random.Random(seed)
        return sum(1 for _ in range(n)
                   if any(leaf.type == "rsi" for leaf in archetype._leaves(explore(rng, cfg).entry)))
    base = bias_config(_brief(most_failing=None))
    biased = bias_config(_brief(most_failing="rsi"))
    assert biased.leaf_type_weights == {"rsi": pytest.approx(0.3)}
    assert _rsi_entry_count(biased) < _rsi_entry_count(base)


# ──────────────────────────── Task 8 — generate_batch ────────────────────────────
from collections import Counter

from webull_api.lab.propose import generate_batch
from webull_api.lab.schema import SeedEntry


def _seeds():
    s = _strat(SmaCross(type="sma_cross", fast=10, slow=50, direction="above"),
               stop_loss_pct=5, take_profit_pct=10)
    return [SeedEntry(fingerprint=fingerprint(s), strategy=s,
                      archetype=archetype.classify(s), why="x", status="confirmed_proven")]


def _cb(s, cfg=DEFAULT_PROPOSE_CONFIG):
    return canon_bucket(s, period_bucket=cfg.bucket_step)


def test_generate_batch_le_total_and_distinct():
    out = generate_batch(_brief(), _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(1),
                         mutation_n=4, explore_n=4)
    assert len(out) <= 8
    fps = [fingerprint(s) for s in out]
    cbs = [_cb(s) for s in out]
    assert len(fps) == len(set(fps))            # no dup fingerprint
    assert len(cbs) == len(set(cbs))            # no dup canon_bucket


def test_generate_batch_excludes_avoid_fingerprints_and_canon_buckets():
    brief0 = _brief()
    b1 = generate_batch(brief0, _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(2),
                        mutation_n=6, explore_n=6)
    avoid_fps = {fingerprint(s) for s in b1}
    avoid_cbs = {_cb(s) for s in b1}
    brief2 = brief0.model_copy(update={"avoid_fingerprints": sorted(avoid_fps),
                                       "avoid_canon_buckets": sorted(avoid_cbs)})
    b2 = generate_batch(brief2, _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(2),
                        mutation_n=6, explore_n=6)
    assert not ({fingerprint(s) for s in b2} & avoid_fps)
    assert not ({_cb(s) for s in b2} & avoid_cbs)


def test_generate_batch_respects_archetype_and_cohort_caps():
    total = 20
    out = generate_batch(_brief(), [], DEFAULT_PROPOSE_CONFIG, random.Random(3),
                         mutation_n=0, explore_n=total)
    arch_cap = round(total * DEFAULT_PROPOSE_CONFIG.lab.max_archetype_frac)
    cohort_cap = round(total * DEFAULT_PROPOSE_CONFIG.lab.max_behavioral_cohort_frac)
    arch_counts = Counter(archetype.classify(s) for s in out)
    cohort_counts = Counter(structural_cohort(s) for s in out)
    assert max(arch_counts.values()) <= arch_cap
    assert max(cohort_counts.values()) <= cohort_cap


def test_generate_batch_cap_bites_trims_to_max_candidates():
    out = generate_batch(_brief(), _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(4),
                         mutation_n=40, explore_n=40)
    assert len(out) <= DEFAULT_PROPOSE_CONFIG.lab.max_candidates_per_batch  # 64


def test_generate_batch_is_deterministic():
    a = generate_batch(_brief(), _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(9),
                       mutation_n=6, explore_n=6)
    b = generate_batch(_brief(), _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(9),
                       mutation_n=6, explore_n=6)
    assert [fingerprint(s) for s in a] == [fingerprint(s) for s in b]


def test_generate_batch_cold_start_ignores_seeds():
    # mutation_n==0 => all-explore: seeds are never consulted, so output is seed-independent.
    a = generate_batch(_brief(), _seeds(), DEFAULT_PROPOSE_CONFIG, random.Random(5),
                       mutation_n=0, explore_n=8)
    b = generate_batch(_brief(), [], DEFAULT_PROPOSE_CONFIG, random.Random(5),
                       mutation_n=0, explore_n=8)
    assert [fingerprint(s) for s in a] == [fingerprint(s) for s in b]


def test_generate_batch_explicit_split_uses_seeds_for_exploit_slots():
    seeds = _seeds()
    out = generate_batch(_brief(), seeds, DEFAULT_PROPOSE_CONFIG, random.Random(3),
                         mutation_n=8, explore_n=0)
    assert 0 < len(out) <= 8
    # every exploit candidate is a mutation distinct from its seed
    assert all(fingerprint(s) != seeds[0].fingerprint for s in out)


def test_rsi_grid_expresses_rsi2_below_10():
    g = propose.LEAF_PARAM_GRID["rsi"]
    assert (2, 3, 5) == tuple(g["period"][:3]) and (5, 10, 15) == tuple(g["below"][:3])
    assert 80 in g["above"] and 90 in g["above"]
    seen = set()
    for i in range(400):
        leaf = propose._make_leaf("rsi_below", random.Random(i))
        seen.add((leaf["period"], leaf["threshold"]))
    assert (2, 10.0) in seen          # the paper book's RSI(2)<10 family is now expressible


# ──────────────────────────── Task 10 — new leaf vocabulary ────────────────────────────
def test_make_leaf_new_tokens_always_valid():
    rng = random.Random(1)
    for tok in ("ibs", "ibs_below", "ibs_above", "zscore", "zscore_below", "zscore_above",
                "drop_from_high", "consec_down"):
        for _ in range(20):
            leaf = propose._make_leaf(tok, rng)
            assert propose._try_validate(
                {"name": "x", "symbol": "SPY", "entry": leaf}) is not None
    assert propose._make_leaf("ibs_below", rng)["side"] == "below"
    assert propose._make_leaf("zscore_above", rng)["comparison"] == "above"


def test_generate_batch_reaches_new_mean_reversion_leaves():
    brief = _brief()
    out = propose.generate_batch(brief, seeds=[], cfg=propose.DEFAULT_PROPOSE_CONFIG,
                                 rng=random.Random(7), mutation_n=0, explore_n=64)
    assert out, "explore must produce candidates"
    types = {leaf.type for s in out for leaf in archetype._leaves(s.entry)}
    assert types & {"ibs", "zscore", "drop_from_high", "consec_down"}, types


def test_swap_sibling_moves_within_grown_mean_revert_family():
    s = Strategy.model_validate({"name": "x", "symbol": "SPY",
        "entry": {"type": "rsi", "period": 2, "threshold": 10.0, "comparison": "below"}})
    seen = set()
    for i in range(60):
        out = propose._swap_sibling(s, random.Random(i), propose.DEFAULT_PROPOSE_CONFIG)
        if out is not None:
            seen.add(out.entry.type)
    assert seen & {"ibs", "zscore", "drop_from_high", "consec_down"}, seen


def test_carry_direction_never_overrides_ibs_side():
    # _swap_sibling copies a stale old-leaf direction/side into the new leaf's `side` field, but
    # ibs's `side` already encodes the token's identity (ibs_above vs ibs_below); overwriting it
    # flips the archetype family. A sided old leaf (macd direction="below") swapped for a fresh
    # ibs(side="above") token must keep "above".
    old = {"type": "macd", "fast": 12, "slow": 26, "signal": 9, "ref": "signal",
           "direction": "below"}
    new = {"type": "ibs", "level": 0.8, "side": "above"}
    propose._carry_direction(old, new)
    assert new["side"] == "above"
