"""Reseed quota / cooldown / lineage helpers + select_seeds (Task 29).

Cooldown ISO strings are compared lexicographically (same-format ISO-8601). Pure, no I/O.
"""
from __future__ import annotations

from webull_api.lab.schema import (DEFAULT_LAB_CONFIG, LabConfig, RegimeTag,  # noqa: F401
                                   ReseedResult, SeedEntry, StrategyRecord)


def cooldown_active(rec: StrategyRecord, as_of: str) -> bool:
    cu = rec.cooldown_until_iso
    return bool(cu) and as_of < cu


def next_cooldown(kills: int, *, cfg: LabConfig = DEFAULT_LAB_CONFIG) -> int | None:
    if kills >= cfg.retire_after_kills:
        return None
    days = cfg.kill_cooldown_days
    idx = max(0, kills - 1)
    return days[idx] if idx < len(days) else days[-1]


def lineage_penalty(rec: StrategyRecord, *, cfg: LabConfig = DEFAULT_LAB_CONFIG) -> float:
    gen = rec.lineage.generation if rec.lineage else rec.generation
    return min(1.0, max(0, gen) * cfg.lineage_penalty)


def explore_quota(coverage_saturation: float, *, cfg: LabConfig = DEFAULT_LAB_CONFIG) -> int:
    sat = min(1.0, max(0.0, coverage_saturation))
    frac = cfg.explore_frac - (cfg.explore_frac - cfg.explore_frac_floor) * sat
    return int(round(cfg.seed_quota * frac))


def archetype_quota(library: list[StrategyRecord], *,
                    cfg: LabConfig = DEFAULT_LAB_CONFIG) -> dict[str, int]:
    cap = int(cfg.seed_quota * cfg.max_archetype_frac)
    return {a: cap for a in sorted({r.archetype for r in library})}


_SEED_STATUSES = ("provisional_proven", "confirmed_proven")


def _seed_rank(rec: StrategyRecord, *, cfg: LabConfig) -> float:
    base = 2.0 if rec.status == "confirmed_proven" else 1.0
    sc = rec.score.score if rec.score else 0.0
    return base + sc / 100.0 - lineage_penalty(rec, cfg=cfg)


def select_seeds(library: list[StrategyRecord], as_of: str, *,
                 cfg: LabConfig = DEFAULT_LAB_CONFIG,
                 regime_now: RegimeTag) -> ReseedResult:  # noqa: ARG001
    eligible = [r for r in library
                if r.status in _SEED_STATUSES and not cooldown_active(r, as_of)]
    eligible.sort(key=lambda r: (-_seed_rank(r, cfg=cfg), r.fingerprint))

    arch_cap = int(cfg.seed_quota * cfg.max_archetype_frac)
    coh_cap = int(cfg.seed_quota * cfg.max_behavioral_cohort_frac)
    arch_count: dict[str, int] = {}
    coh_count: dict[str, int] = {}
    seeds: list[SeedEntry] = []
    for r in eligible:
        if len(seeds) >= cfg.seed_quota:
            break
        a, c = r.archetype, (r.behavioral_cohort or "none")
        if arch_count.get(a, 0) >= arch_cap or coh_count.get(c, 0) >= coh_cap:
            continue
        arch_count[a] = arch_count.get(a, 0) + 1
        coh_count[c] = coh_count.get(c, 0) + 1
        seeds.append(SeedEntry(fingerprint=r.fingerprint, strategy=r.strategy,
                               archetype=r.archetype,
                               why=(r.lineage.why if r.lineage else ""),
                               status=r.status))

    saturation = (len(seeds) / cfg.seed_quota) if cfg.seed_quota else 1.0
    return ReseedResult(seeds=seeds, explore_quota=explore_quota(saturation, cfg=cfg),
                        archetype_quota=archetype_quota(library, cfg=cfg))
