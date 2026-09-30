"""Pure self-tuning stagnation state machine for the autopilot lab loop.

`update_stagnation` returns a NEW StagnationState given this cycle's accepted-by-archetype counts +
observed Gate-A pass rate: per-archetype dry streaks, a throttle that decays geometrically after
`stagnation_patience` dry cycles and snaps back to 1.0 on a fresh passer, and an EWMA pass-rate.
NO I/O, NO global random, NO time; NEVER imports webull_api.trading or any order surface."""
from __future__ import annotations

from webull_api.lab.schema import DEFAULT_LAB_CONFIG, LabConfig, StagnationState


def update_stagnation(prev: StagnationState, *, accepted_by_archetype: dict[str, int],
                      total_accepted: int, observed_pass_rate: float | None,
                      archetypes_present: set[str], cycle_date: str,
                      cfg: LabConfig = DEFAULT_LAB_CONFIG) -> StagnationState:
    overall = prev.overall_dry_streak + 1 if total_accepted == 0 else 0

    # carry everything; overwrite only the archetypes present this cycle
    per_arch = dict(prev.per_archetype_dry)
    throttle = dict(prev.throttle)
    for a in archetypes_present:
        if accepted_by_archetype.get(a, 0) > 0:                 # fresh passer
            per_arch[a] = 0
            throttle[a] = cfg.throttle_recover
        else:
            per_arch[a] = prev.per_archetype_dry.get(a, 0) + 1
            if per_arch[a] >= cfg.stagnation_patience:
                throttle[a] = max(cfg.throttle_floor,
                                  prev.throttle.get(a, 1.0) * cfg.throttle_decay)
            else:
                throttle[a] = prev.throttle.get(a, 1.0)

    if observed_pass_rate is None:
        ewma = prev.pass_rate_ewma
    elif prev.pass_rate_ewma == 0:
        ewma = observed_pass_rate
    else:
        ewma = (cfg.pass_rate_alpha * observed_pass_rate
                + (1 - cfg.pass_rate_alpha) * prev.pass_rate_ewma)

    return StagnationState(
        overall_dry_streak=overall, per_archetype_dry=per_arch, throttle=throttle,
        pass_rate_ewma=ewma, cycles_run=prev.cycles_run + 1, last_cycle_date=cycle_date)
