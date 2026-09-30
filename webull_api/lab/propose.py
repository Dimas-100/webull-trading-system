"""Mechanical, deterministic, zero-token strategy proposer (no LLM). Pure: every function is a
deterministic transform of its inputs + an injected random.Random — NO module-level random/time/IO.
Output is list[Strategy], drop-in for orchestrator.ingest_proposals. Imports only the pure lab
engine; never webull_api.trading."""
from __future__ import annotations

import copy
import dataclasses
import random
from collections import Counter
from typing import Any

from pydantic import TypeAdapter, ValidationError

from webull_api.lab import archetype
from webull_api.lab.dedup import canon_bucket, fingerprint
from webull_api.lab.schema import (
    DEFAULT_LAB_CONFIG,
    LAB_BASKET,
    LabConfig,
    ProposalBrief,
    SeedEntry,
    _INT_FIELDS,
)
from webull_api.strategy.schema import Condition, Strategy, leaf_count

# ──────────────────────────── constants (spec §5.1 grids) ────────────────────────────
EVENT_LEAVES: tuple[str, ...] = ("sma_cross", "ema_cross", "breakout", "rsi", "macd",
                                 "ibs", "zscore", "drop_from_high", "consec_down")
STATE_LEAVES: tuple[str, ...] = ("price_vs_sma", "atr_pct")

# inverse of archetype._leaf_archetype (rsi/ibs/zscore split by side/comparison). Used by
# _swap_sibling.
SIBLING_GROUPS: dict[str, tuple[str, ...]] = {
    "trend_follow": ("sma_cross", "ema_cross", "price_vs_sma", "atr_pct"),
    "momentum": ("rsi_above", "macd", "ibs_above", "zscore_above"),
    "mean_revert": ("rsi_below", "ibs_below", "zscore_below", "drop_from_high", "consec_down"),
    "breakout": ("breakout",),
}

# round-number per-leaf parameter ranges (explore + bump targets). slow > fast enforced by Strategy.
LEAF_PARAM_GRID: dict = {
    "sma_cross": {"fast": (5, 10, 15, 20, 30, 50), "slow": (20, 50, 100, 150, 200)},
    "ema_cross": {"fast": (5, 10, 15, 20, 30, 50), "slow": (20, 50, 100, 150, 200)},
    "rsi": {"period": (2, 3, 5, 7, 10, 14, 20), "below": (5, 10, 15, 20, 25, 30),
            "above": (65, 70, 75, 80, 90)},
    "breakout": {"lookback": (10, 20, 30, 40, 55)},
    "price_vs_sma": {"period": (20, 50, 100, 150, 200)},
    "macd": {"triples": ((12, 26, 9), (8, 21, 5), (5, 35, 5))},   # (fast, slow, signal)
    "atr_pct": {"period": (14, 20), "level": (1, 1.5, 2, 2.5, 3, 4)},
    "ibs": {"below": (0.1, 0.15, 0.2, 0.25), "above": (0.75, 0.8, 0.9)},
    "zscore": {"period": (10, 20, 50), "below": (-1.5, -2.0, -2.5), "above": (1.5, 2.0, 2.5)},
    "drop_from_high": {"lookback": (10, 20, 50, 100), "pct": (3, 5, 8, 10, 15)},
    "consec_down": {"count": (3, 4, 5, 6)},
}

# event leaf token -> archetype name (for archetype-weighted selection in explore). Coarse
# selection-weighting only — the sided momentum variants (rsi_above/ibs_above/zscore_above) are
# still reachable via _make_leaf's random side.
_EVENT_LEAF_ARCH: dict[str, str] = {
    "sma_cross": "trend_follow", "ema_cross": "trend_follow",
    "breakout": "breakout", "rsi": "momentum", "macd": "momentum",
    "ibs": "mean_revert", "zscore": "mean_revert",
    "drop_from_high": "mean_revert", "consec_down": "mean_revert",
}

_LEAF_ADAPTER = TypeAdapter(Condition)


# ──────────────────────────── config ────────────────────────────
@dataclasses.dataclass(frozen=True)
class ProposeConfig:
    lab: LabConfig = DEFAULT_LAB_CONFIG          # single source for shared knobs; read, never copied
    universe: tuple[str, ...] = LAB_BASKET
    default_symbol: str = "SPY"                  # == universe[0]
    timeframe: str = "1D"
    weekly_frac: float = 0.0
    bucket_step: int = 5                         # == lab.canon_period_bucket
    max_attempts_per_slot: int = 8
    p_add_filter: float = 0.5
    p_add_exit: float = 0.3
    p_add_stops: float = 0.6
    p_two_leaf_entry: float = 0.35
    fail_bias_factor: float = 0.3
    archetype_floor_weight: float = 0.15
    stop_grid: tuple[float, ...] = (3, 4, 5, 6, 8, 10)
    target_grid: tuple[float, ...] = (6, 8, 10, 12, 15, 20)
    archetype_weights: dict[str, float] | None = None   # None == uniform; filled by bias_config
    leaf_type_weights: dict[str, float] | None = None   # None == uniform; filled by bias_config


DEFAULT_PROPOSE_CONFIG = ProposeConfig()


# ──────────────────────────── pure helpers ────────────────────────────
def _shuffled(seq, rng: random.Random) -> list:
    out = list(seq)
    rng.shuffle(out)
    return out


def _weighted_choice(items: list, weights: list[float], rng: random.Random) -> Any:
    total = sum(weights)
    if total <= 0:
        return items[rng.randrange(len(items))]
    r = rng.random() * total
    upto = 0.0
    for it, w in zip(items, weights):
        upto += w
        if r < upto:
            return it
    return items[-1]


def _try_validate(dump: dict) -> Strategy | None:
    try:
        return Strategy.model_validate(dump)
    except ValidationError:
        return None


def _make_leaf(token: str, rng: random.Random) -> dict:
    """Build a model_dump-style leaf dict from LEAF_PARAM_GRID for a SIBLING/EVENT/STATE token."""
    if token in ("sma_cross", "ema_cross"):
        g = LEAF_PARAM_GRID[token]
        slow = rng.choice(g["slow"])
        fast = rng.choice([f for f in g["fast"] if f < slow])
        return {"type": token, "fast": fast, "slow": slow,
                "direction": rng.choice(("above", "below"))}
    if token in ("rsi", "rsi_above", "rsi_below"):
        g = LEAF_PARAM_GRID["rsi"]
        comp = ("above" if token == "rsi_above"
                else "below" if token == "rsi_below"
                else rng.choice(("above", "below")))
        thr = rng.choice(g["above"] if comp == "above" else g["below"])
        return {"type": "rsi", "period": rng.choice(g["period"]),
                "threshold": float(thr), "comparison": comp}
    if token == "breakout":
        return {"type": "breakout", "lookback": rng.choice(LEAF_PARAM_GRID["breakout"]["lookback"]),
                "direction": rng.choice(("high", "low"))}
    if token == "price_vs_sma":
        return {"type": "price_vs_sma", "period": rng.choice(LEAF_PARAM_GRID["price_vs_sma"]["period"]),
                "side": rng.choice(("above", "below"))}
    if token == "macd":
        fast, slow, signal = rng.choice(LEAF_PARAM_GRID["macd"]["triples"])
        return {"type": "macd", "fast": fast, "slow": slow, "signal": signal,
                "ref": "signal", "direction": rng.choice(("above", "below"))}
    if token == "atr_pct":
        return {"type": "atr_pct", "period": rng.choice(LEAF_PARAM_GRID["atr_pct"]["period"]),
                "level": float(rng.choice(LEAF_PARAM_GRID["atr_pct"]["level"])),
                "side": rng.choice(("above", "below"))}
    if token in ("ibs", "ibs_below", "ibs_above"):
        g = LEAF_PARAM_GRID["ibs"]
        side = ("below" if token == "ibs_below" else "above" if token == "ibs_above"
                else rng.choice(("below", "above")))
        return {"type": "ibs", "level": float(rng.choice(g[side])), "side": side}
    if token in ("zscore", "zscore_below", "zscore_above"):
        g = LEAF_PARAM_GRID["zscore"]
        comp = ("below" if token == "zscore_below" else "above" if token == "zscore_above"
                else rng.choice(("below", "above")))
        return {"type": "zscore", "period": rng.choice(g["period"]),
                "threshold": float(rng.choice(g[comp])), "comparison": comp}
    if token == "drop_from_high":
        g = LEAF_PARAM_GRID["drop_from_high"]
        return {"type": "drop_from_high", "lookback": rng.choice(g["lookback"]),
                "pct": float(rng.choice(g["pct"]))}
    if token == "consec_down":
        return {"type": "consec_down",
                "count": rng.choice(LEAF_PARAM_GRID["consec_down"]["count"])}
    raise ValueError(f"unknown leaf token {token!r}")


# ──────────────────────────── curve-free diversity signature ────────────────────────────
def structural_cohort(strategy: Strategy) -> str:
    """Per-batch cohort-cap proxy (DISTINCT from dedup.behavioral_cohort, which needs a curve)."""
    types = "+".join(sorted(leaf.type for leaf in archetype._leaves(strategy.entry)))
    return (f"{archetype.classify(strategy)}|{types}"
            f"|filt:{strategy.filter is not None}|exit:{strategy.exit is not None}")


# ──────────────────────────── explore (fresh candidate) ────────────────────────────
def _event_weight(token: str, cfg: ProposeConfig) -> float:
    aw = (cfg.archetype_weights or {}).get(_EVENT_LEAF_ARCH[token], 1.0)
    lw = (cfg.leaf_type_weights or {}).get(token, 1.0)
    return max(0.0, aw) * max(0.0, lw)


def _pick_event_token(rng: random.Random, cfg: ProposeConfig) -> str:
    toks = list(EVENT_LEAVES)
    return _weighted_choice(toks, [_event_weight(t, cfg) for t in toks], rng)


def explore(rng: random.Random, cfg: ProposeConfig = DEFAULT_PROPOSE_CONFIG) -> Strategy:
    """Fresh bounded-random candidate. EVENT entry (single, or depth-1 2-leaf composite at
    p_two_leaf_entry), optional STATE filter / EVENT exit / stops at their probs. Always valid;
    leaf budget <=6 by construction (entry<=2 + filter<=1 + exit<=1)."""
    if rng.random() < cfg.p_two_leaf_entry:
        t1 = _pick_event_token(rng, cfg)
        rest = [t for t in EVENT_LEAVES if t != t1]
        t2 = _weighted_choice(rest, [_event_weight(t, cfg) for t in rest], rng)
        comp_type = rng.choice(("all_of", "any_of"))
        entry = {"type": comp_type, "conditions": [_make_leaf(t1, rng), _make_leaf(t2, rng)]}
    else:
        entry = _make_leaf(_pick_event_token(rng, cfg), rng)

    node: dict = {"name": f"lab_{rng.randrange(1_000_000):06d}", "symbol": cfg.default_symbol,
                  "timeframe": cfg.timeframe, "entry": entry, "origin": "lab_generated"}
    if rng.random() < cfg.p_add_filter:
        node["filter"] = _make_leaf(rng.choice(STATE_LEAVES), rng)
    if rng.random() < cfg.p_add_exit:
        node["exit"] = _make_leaf(_pick_event_token(rng, cfg), rng)
    if rng.random() < cfg.p_add_stops:
        node["stop_loss_pct"] = float(rng.choice(cfg.stop_grid))
        node["take_profit_pct"] = float(rng.choice(cfg.target_grid))
    return Strategy.model_validate(node)


# ──────────────────────────── mutate operators ────────────────────────────
def _leaf_model(leaf: dict):
    return _LEAF_ADAPTER.validate_python(leaf)


def _is_token(leaf: dict, token: str) -> bool:
    t = leaf.get("type")
    if token == "rsi_above":
        return t == "rsi" and leaf.get("comparison") == "above"
    if token == "rsi_below":
        return t == "rsi" and leaf.get("comparison") == "below"
    if token in ("ibs_above", "ibs_below"):
        return t == "ibs" and leaf.get("side") == token.rsplit("_", 1)[1]
    if token in ("zscore_above", "zscore_below"):
        return t == "zscore" and leaf.get("comparison") == token.rsplit("_", 1)[1]
    return t == token


def _carry_direction(old: dict, new: dict) -> None:
    val = old.get("direction") if old.get("direction") in ("above", "below") else old.get("side")
    if val not in ("above", "below"):
        return
    if "direction" in new and new.get("type") != "breakout":
        new["direction"] = val
    # ibs's `side` already encodes the token's sidedness (ibs_above/ibs_below) via _make_leaf;
    # overwriting it with the OLD leaf's direction/side would flip the new token's identity
    # (e.g. ibs_above -> side "below") and thus its archetype family. Never carry into ibs.
    elif "side" in new and new.get("type") != "ibs":
        new["side"] = val


def _int_field_targets(dump: dict) -> list[tuple[str, int | None, str, int]]:
    out: list[tuple[str, int | None, str, int]] = []
    for slot in ("entry", "exit", "filter"):
        node = dump.get(slot)
        if not node:
            continue
        is_comp = node.get("type") in ("all_of", "any_of")
        leaves = node["conditions"] if is_comp else [node]
        for li, leaf in enumerate(leaves):
            for f in _INT_FIELDS:
                if isinstance(leaf.get(f), int):
                    out.append((slot, li if is_comp else None, f, leaf[f]))
    return out


def _set_field(dump: dict, slot: str, idx: int | None, field: str, val: int) -> None:
    target = dump[slot]["conditions"][idx] if idx is not None else dump[slot]
    target[field] = val


def _bump_candidates(dump, slot, idx, field, val, cfg, rng) -> list[int]:
    leaf = dump[slot]["conditions"][idx] if idx is not None else dump[slot]
    grid = LEAF_PARAM_GRID.get(leaf.get("type"), {})
    opts = [g for g in grid.get(field, ()) if abs(g - val) >= cfg.bucket_step]
    rng.shuffle(opts)
    return list(opts) + [val + cfg.bucket_step, val - cfg.bucket_step]   # full-step => canon moves


def _bump_int(strategy, rng, cfg):
    base_fp = fingerprint(strategy)
    dump = strategy.model_dump()
    for slot, idx, field, val in _shuffled(_int_field_targets(dump), rng):
        for newval in _bump_candidates(dump, slot, idx, field, val, cfg, rng):
            if newval < 1:
                continue
            cand = copy.deepcopy(dump)
            _set_field(cand, slot, idx, field, int(newval))
            s = _try_validate(cand)
            if s is not None and fingerprint(s) != base_fp:
                return s
    return None


def _swap_sibling(strategy, rng, cfg):
    base_fp = fingerprint(strategy)
    dump = strategy.model_dump()
    targets = []
    for slot in ("entry", "exit", "filter"):
        node = dump.get(slot)
        if not node:
            continue
        is_comp = node.get("type") in ("all_of", "any_of")
        leaves = node["conditions"] if is_comp else [node]
        for li, leaf in enumerate(leaves):
            targets.append((slot, li if is_comp else None, leaf, leaves))
    for slot, idx, leaf, leaves in _shuffled(targets, rng):
        group = archetype._leaf_archetype(_leaf_model(leaf))
        sibs = [t for t in SIBLING_GROUPS.get(group, ()) if not _is_token(leaf, t)]
        for tok in _shuffled(sibs, rng):
            new_leaf = _make_leaf(tok, rng)
            _carry_direction(leaf, new_leaf)
            if idx is not None:
                others = [c for j, c in enumerate(leaves) if j != idx]
                if any(c == new_leaf for c in others):
                    continue
            cand = copy.deepcopy(dump)
            if idx is not None:
                cand[slot]["conditions"][idx] = new_leaf
            else:
                cand[slot] = new_leaf
            s = _try_validate(cand)
            if s is not None and fingerprint(s) != base_fp:
                return s
    return None


def _toggle_filter(strategy, rng, cfg):
    base_fp = fingerprint(strategy)
    dump = strategy.model_dump()
    total = leaf_count(strategy.entry) + leaf_count(strategy.exit) + leaf_count(strategy.filter)
    if dump.get("filter") is None and total >= 6:
        return None
    if dump.get("filter") is not None and rng.random() < 0.5:
        cand = copy.deepcopy(dump)
        cand["filter"] = None
        s = _try_validate(cand)
        if s is not None and fingerprint(s) != base_fp:
            return s
    for tok in _shuffled(STATE_LEAVES, rng):
        cand = copy.deepcopy(dump)
        cand["filter"] = _make_leaf(tok, rng)
        s = _try_validate(cand)
        if s is not None and fingerprint(s) != base_fp:
            return s
    return None


def _adjust_stops(strategy, rng, cfg):
    """Guaranteed-success fallback: stops are raw-hashed in canon_bucket, so a changed grid value
    always escapes BOTH fingerprint and canon dedup. At least one of stop/target changes."""
    dump = strategy.model_dump()
    cur_sl, cur_tp = dump.get("stop_loss_pct"), dump.get("take_profit_pct")
    new_sl = rng.choice([v for v in cfg.stop_grid if v != cur_sl] or list(cfg.stop_grid))
    new_tp = rng.choice([v for v in cfg.target_grid if v != cur_tp] or list(cfg.target_grid))
    dump["stop_loss_pct"] = float(new_sl)
    dump["take_profit_pct"] = float(new_tp)
    return Strategy.model_validate(dump)


def mutate(strategy: Strategy, rng: random.Random, *,
           cfg: ProposeConfig = DEFAULT_PROPOSE_CONFIG) -> Strategy:
    """Apply ONE operator (chosen by rng.shuffle over the applicable set); return the first that
    yields a valid Strategy whose fingerprint != input's. Fallback (forced _adjust_stops) guarantees
    a valid, distinct result. Never raises."""
    base_fp = fingerprint(strategy)
    for op in _shuffled((_bump_int, _swap_sibling, _toggle_filter, _adjust_stops), rng):
        out = op(strategy, rng, cfg)
        if out is not None and fingerprint(out) != base_fp:
            return out
    return _adjust_stops(strategy, rng, cfg)   # forced fallback (always distinct)


# ──────────────────────────── bias (brief feedback -> weights) ────────────────────────────
def bias_config(brief: ProposalBrief, cfg: ProposeConfig = DEFAULT_PROPOSE_CONFIG) -> ProposeConfig:
    """Pure dataclasses.replace turning brief feedback into selection weights (the 'C' wiring).
    Throttle (never ban) mined-out archetypes via 1-kill_rate; down-weight a failing leaf type;
    nudge p_add_filter up in a high-vol regime. Called once by generate_batch."""
    aw: dict[str, float] = {}
    for agg in brief.archetype_stats:
        kill_rate = agg.lifetime_killed / max(1, agg.lifetime_proposed)
        aw[agg.archetype] = max(cfg.archetype_floor_weight, 1.0 - kill_rate)

    lw: dict[str, float] = {}
    fb = brief.feedback
    implicated: set[str] = set()
    if fb.most_failing_condition:
        implicated.add(fb.most_failing_condition)
    for code in fb.common_fail_codes:
        if code in EVENT_LEAVES or code in STATE_LEAVES:   # only codes that name a leaf type
            implicated.add(code)
    for t in implicated:
        lw[t] = cfg.fail_bias_factor

    p_filter = (min(1.0, cfg.p_add_filter + 0.2)
                if brief.regime_now.vol == "high" else cfg.p_add_filter)
    return dataclasses.replace(cfg, archetype_weights=aw or None,
                               leaf_type_weights=lw or None, p_add_filter=p_filter)


# ──────────────────────────── batch generation (cycle owns the split) ────────────────────────────
def _weighted_seed_pick(seeds: list[SeedEntry], rng: random.Random, cfg: ProposeConfig) -> SeedEntry:
    """Round-robin that down-weights seeds whose archetype is throttled (low archetype_weights)."""
    return _weighted_choice(
        list(seeds),
        [max(0.0, (cfg.archetype_weights or {}).get(s.archetype, 1.0)) for s in seeds],
        rng,
    )


def generate_batch(brief: ProposalBrief, seeds: list[SeedEntry], cfg: ProposeConfig,
                   rng: random.Random, *, mutation_n: int, explore_n: int) -> list[Strategy]:
    """Generate <= total distinct candidates. The explore/exploit split is OWNED by the cycle's
    decide_batch and passed in explicitly — this function does NOT re-derive it. Never raises,
    never emits a dup."""
    cfg = bias_config(brief, cfg)
    cap = cfg.lab.max_candidates_per_batch
    total = min(mutation_n + explore_n, cap)
    mut_n, exp_n = mutation_n, explore_n
    while mut_n + exp_n > total:            # if the cap bites, trim the larger half first
        if mut_n >= exp_n:
            mut_n -= 1
        else:
            exp_n -= 1

    slots = ["exploit"] * mut_n + ["explore"] * exp_n
    rng.shuffle(slots)

    avoid_fp = set(brief.avoid_fingerprints)
    avoid_cb = set(brief.avoid_canon_buckets)
    arch_cap = round(total * cfg.lab.max_archetype_frac)
    cohort_cap = round(total * cfg.lab.max_behavioral_cohort_frac)

    out: list[Strategy] = []
    seen_fp: set[str] = set()
    seen_cb: set[str] = set()
    arch_counts: Counter = Counter()
    cohort_counts: Counter = Counter()

    for slot in slots:
        for _ in range(cfg.max_attempts_per_slot):
            if slot == "exploit" and seeds:
                cand = mutate(_weighted_seed_pick(seeds, rng, cfg).strategy, rng, cfg=cfg)
            else:
                cand = explore(rng, cfg)
            fp = fingerprint(cand)
            cb = canon_bucket(cand, period_bucket=cfg.bucket_step)
            if fp in avoid_fp or fp in seen_fp:
                continue
            if cb in avoid_cb or cb in seen_cb:
                continue
            arch = archetype.classify(cand)
            coh = structural_cohort(cand)
            if arch_counts[arch] >= arch_cap or cohort_counts[coh] >= cohort_cap:
                continue
            out.append(cand)
            seen_fp.add(fp)
            seen_cb.add(cb)
            arch_counts[arch] += 1
            cohort_counts[coh] += 1
            break
    return out
