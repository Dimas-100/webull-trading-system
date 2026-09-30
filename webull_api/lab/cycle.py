"""Pure cycle conductor for the autopilot lab loop. Composes the already-merged orchestrator /
propose / trial / dedup / regime engines into one deterministic, resumable cycle. NO I/O, NO global
random, NO time — every side effect is an injected callable (mirrors orchestrator.advance /
exits.scan). NEVER imports webull_api.trading or any order surface (M5 source-guards prove it)."""
from __future__ import annotations

import random
from math import ceil
from statistics import mean

from webull_api.lab import dedup, orchestrator, propose, trial as _trial
from webull_api.lab import stagnation as _stagnation
from webull_api.lab.gate_a import NotEnoughData
from webull_api.lab.propose import ProposeConfig
from webull_api.lab.regime import compute_regime
from webull_api.lab.schema import (BatchDecision, CycleReport, DEFAULT_GATE_A_CONFIG,
                                   DEFAULT_GATE_B_CONFIG, DEFAULT_LAB_CONFIG, GateAConfig,
                                   GateBConfig, IngestResult, LabConfig, ReseedResult,
                                   SeedEntry, StagnationState, StrategyRecord, TrialBook)
from webull_api.strategy.cost import CostModel


def _by_arch(accepted_fps: list[str], library) -> dict[str, int]:
    """archetype -> count over library records whose fingerprint/id is in accepted_fps."""
    fps = set(accepted_fps)
    out: dict[str, int] = {}
    for r in library:
        if r.fingerprint in fps or r.id in fps:
            out[r.archetype] = out.get(r.archetype, 0) + 1
    return out


def _pass_rate(ing: IngestResult) -> float | None:
    screened = len(ing.accepted) + len(ing.gate_a_failed)
    if screened == 0:
        return None
    return len(ing.accepted) / screened


def decide_batch(library, *, seeds: list[SeedEntry], reseed_result: ReseedResult,
                 stagnation: StagnationState, cfg: LabConfig) -> BatchDecision:
    active = sum(1 for r in library if r.status == "proving" and r.trial_id)
    waiting = sum(1 for r in library if r.status == "proving" and not r.trial_id)
    open_slots = max(0, cfg.max_concurrent_proving - active - waiting)
    cold_start = active == 0 and len(seeds) == 0
    pr = min(1.0, max(cfg.pass_rate_floor,
                      stagnation.pass_rate_ewma if stagnation.pass_rate_ewma > 0
                      else cfg.pass_rate_default))
    mean_throttle = mean(stagnation.throttle.values()) if stagnation.throttle else 1.0
    explore_n = max(1, round(reseed_result.explore_quota * (2 - mean_throttle)))
    if cold_start:
        mutation_n = 0
        explore_n = min(cfg.max_candidates_per_batch, ceil(open_slots / pr))
    else:
        mutation_n = max(0, min(ceil(open_slots / pr), cfg.max_candidates_per_batch - explore_n))
    if open_slots == 0:
        mutation_n = explore_n = 0
    cohort_caps = {c: int(cfg.max_concurrent_proving * cfg.max_behavioral_cohort_frac)
                   for c in {r.behavioral_cohort or "none" for r in library}}
    return BatchDecision(active_trials=active, admitted_waiting=waiting, open_slots=open_slots,
                         pass_rate_est=pr, mutation_n=mutation_n, explore_n=explore_n,
                         max_admit=open_slots, cohort_caps=cohort_caps, cold_start=cold_start)


def _assign_cohorts(library, books: dict[str, TrialBook], cfg: LabConfig) -> None:
    """Mutate rec.behavioral_cohort IN PLACE for records whose trial book holds
    >= cfg.cohort_min_curve_points equity-curve points; leave the rest unchanged."""
    for rec in library:
        if not rec.trial_id:
            continue
        book = books.get(rec.trial_id)
        if book is None or len(book.equity_curve) < cfg.cohort_min_curve_points:
            continue
        rec.behavioral_cohort = dedup.behavioral_cohort(book.equity_curve)


def _open_admitted(library, *, get_bars, save_state, gate_a_cfg: GateAConfig,
                   today_et: str, now_iso: str, m: int, cap: int,
                   min_ok_frac: float = 0.75) -> list[str]:
    """Open a forward trial for each proving record WITHOUT a trial_id, highest Gate-A score first,
    while active(#proving with trial_id) < cap. NotEnoughData on a record -> skip (never abort)."""
    pending = [r for r in library if r.status == "proving" and not r.trial_id]
    pending.sort(key=lambda r: (-(r.gate_a.dsr if r.gate_a else 0.0),
                                -(r.gate_a.pooled_expectancy if r.gate_a else 0.0), r.id))
    active = sum(1 for r in library if r.status == "proving" and r.trial_id)
    bars, _ = orchestrator.fetch_basket_bars(get_bars, count=gate_a_cfg.min_lookback_bars,
                                             min_ok_frac=min_ok_frac)
    opened: list[str] = []
    for rec in pending:
        if active >= cap:
            break
        try:
            state = _trial.open_trial(rec.strategy, bars, trial_id=rec.id, now_iso=now_iso,
                                      inception_et_date=today_et, gate_a=rec.gate_a, m=m)
        except NotEnoughData:
            continue
        save_state(state)
        rec.trial_id = rec.id
        active += 1
        opened.append(rec.id)
    return opened


def run_cycle(library: list[StrategyRecord], *,
              cfg: LabConfig = DEFAULT_LAB_CONFIG,
              gate_a_cfg: GateAConfig = DEFAULT_GATE_A_CONFIG,
              gate_b_cfg: GateBConfig = DEFAULT_GATE_B_CONFIG,
              cost: CostModel, get_bars, load_state, save_state, load_books,
              today_et: str, now_iso: str, m: int, tombstones: list[dict],
              stagnation: StagnationState, rng: random.Random
              ) -> tuple[list[StrategyRecord], CycleReport]:
    """Pure brain of one autopilot cycle: advance -> assign cohorts -> reseed/brief -> decide_batch
    -> generate_batch -> ingest(max_admit) -> open admitted trials -> update stagnation -> report.
    reseed is called ONCE (proposer seeds AND the cycle's fresh survivor snapshot). m_after /
    stagnation / events live ON the report. cycle_seq is left 0 for the shell to overwrite."""
    m_before = m

    adv = orchestrator.advance(library, cfg=cfg, gate_a=gate_a_cfg, gate_b=gate_b_cfg, cost=cost,
                               get_bars=get_bars, today_et=today_et, now_iso=now_iso,
                               load_state=load_state, save_state=save_state, m=m)

    _assign_cohorts(library, load_books(), cfg)
    regime = compute_regime(orchestrator.first_available_bars(
        get_bars, count=gate_a_cfg.min_lookback_bars))
    rr = orchestrator.reseed(library, now_iso, cfg=cfg, regime_now=regime)
    brief = orchestrator.build_proposal_brief(library, now_iso, regime_now=regime,
                                              reseed_result=rr, cfg=cfg, tombstones=tombstones)

    decision = decide_batch(library, seeds=rr.seeds, reseed_result=rr,
                            stagnation=stagnation, cfg=cfg)
    candidates = propose.generate_batch(brief, rr.seeds, ProposeConfig(lab=cfg), rng,
                                        mutation_n=decision.mutation_n, explore_n=decision.explore_n)
    ing = orchestrator.ingest_proposals(library, candidates, now_iso, cfg=cfg, gate_cfg=gate_a_cfg,
                                        cost=cost, get_bars=get_bars, m=m, max_admit=decision.max_admit)
    new_proving = _open_admitted(library, get_bars=get_bars, save_state=save_state,
                                 gate_a_cfg=gate_a_cfg, today_et=today_et, now_iso=now_iso,
                                 m=ing.m_after, cap=cfg.max_concurrent_proving,
                                 min_ok_frac=cfg.basket_min_ok_frac)
    stagnation2 = _stagnation.update_stagnation(
        stagnation, accepted_by_archetype=_by_arch(ing.accepted, library),
        total_accepted=len(ing.accepted), observed_pass_rate=_pass_rate(ing),
        archetypes_present={r.archetype for r in library}, cycle_date=today_et, cfg=cfg)

    proving_active = sum(1 for r in library if r.status == "proving" and r.trial_id)
    events = [{"type": "gate_a_failed", "fingerprint": f["fingerprint"],
               "fail_codes": f.get("fail_codes", []), "at_iso": now_iso}
              for f in ing.gate_a_failed]
    report = CycleReport(
        cycle_seq=0, cycle_date=today_et, ran_at_iso=now_iso, regime=regime,
        no_op=adv.no_op, no_bar=adv.no_op, advanced=adv.advanced, promoted=adv.promoted,
        killed=adv.killed, generated=len(candidates),
        explore_generated=min(decision.explore_n, len(candidates)),
        screened=ing.m_after - m_before, duplicates=len(ing.duplicates),
        gate_a_failed=ing.gate_a_failed, accepted=ing.accepted, new_proving=new_proving,
        proving_active=proving_active, slots_cap=cfg.max_concurrent_proving,
        free_slots=max(0, cfg.max_concurrent_proving - proving_active),
        backlog_submitted=len(new_proving), decision=decision, m_before=m_before,
        m_after=ing.m_after, stagnation=stagnation2,
        throttled_archetypes=sorted(a for a, t in stagnation2.throttle.items() if t < 1.0),
        events=events, errors=[], elapsed_ms=0)
    return library, report
