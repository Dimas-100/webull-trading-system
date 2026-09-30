"""Propose-and-prove orchestrator (pure; dependencies injected like exits.scan).

Modules gate_a/_trial/_verdict are imported AS MODULES (not names) so they stay monkeypatchable.
get_bars(symbol, "D", count) is injected; MarketDataNotEntitledError propagates. No order surface
is imported anywhere (proven by M6 source-guards).
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta

from webull_api.lab import archetype, dedup, reseed as _reseed
from webull_api.lab import scoring as _scoring
from webull_api.lab import gate_a  # module attr -> gate_a.screen_one (patchable)
from webull_api.lab import trial as _trial      # module attr -> _trial.advance (patchable)
from webull_api.lab import verdict as _verdict  # module attr -> _verdict.evaluate_verdict
from webull_api.lab.schema import (ArchetypeAggregate, LAB_BASKET, OBJECTIVE_TEXT,
                                   AdvanceResult, GateAConfig, GateBConfig, IngestResult,
                                   LabConfig, ProposalBrief, RegimeTag, ReseedResult,
                                   ShapedFeedback, StrategyRecord)
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.bars import to_ohlcv
from webull_api.strategy.cost import CostModel

_KILLED = ("rejected", "retired")


class InsufficientBasketData(RuntimeError):
    """Too few LAB_BASKET symbols returned bars to run a meaningful (breadth-checked) cycle — a real
    market-data outage, distinct from one flaky symbol (which is tolerated + dropped).
    MarketDataNotEntitledError is a separate entitlement failure that always propagates first."""


def fetch_basket_bars(get_bars, *, count: int, symbols=LAB_BASKET,
                      min_ok_frac: float = 0.75) -> tuple[dict, list[str]]:
    """Fetch daily OHLCV per symbol, TOLERATING per-symbol failures so one flaky symbol (common on a
    home connection) never aborts the whole cycle. Entitlement errors propagate (a hard abort).
    Returns (bars_by_symbol, dropped_symbols); raises InsufficientBasketData when fewer than
    ceil(min_ok_frac * n) symbols succeed."""
    bars: dict = {}
    dropped: list[str] = []
    for sym in symbols:
        try:
            bars[sym] = to_ohlcv(get_bars(sym, "D", count=str(count)))
        except MarketDataNotEntitledError:
            raise
        except Exception:
            dropped.append(sym)
    need = max(1, math.ceil(min_ok_frac * len(symbols)))
    if len(bars) < need:
        raise InsufficientBasketData(
            f"only {len(bars)}/{len(symbols)} basket symbols returned bars "
            f"(need >= {need}); dropped {dropped}")
    return bars, dropped


def first_available_bars(get_bars, *, count: int, symbols=LAB_BASKET):
    """OHLCV for the first symbol that fetches — used for the coarse market regime, where any liquid
    basket symbol is a fine proxy, so a down reference symbol doesn't abort the cycle. Entitlement
    propagates; raises InsufficientBasketData if none succeed."""
    for sym in symbols:
        try:
            return to_ohlcv(get_bars(sym, "D", count=str(count)))
        except MarketDataNotEntitledError:
            raise
        except Exception:
            continue
    raise InsufficientBasketData("no LAB_BASKET symbol returned bars for the regime probe")


def _add_days(now_iso: str, days: int) -> str:
    try:
        dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.fromisoformat(now_iso[:10])
    return (dt + timedelta(days=days)).isoformat()


def _upsert(library: list[StrategyRecord], rec: StrategyRecord) -> None:
    for i, existing in enumerate(library):
        if existing.fingerprint == rec.fingerprint:
            library[i] = rec
            return
    library.append(rec)


def _new_record(strategy, fp, cb, as_of, report, *, m: int) -> StrategyRecord:
    arch = archetype.classify(strategy)
    bucket = report.regime_buckets[0] if report.regime_buckets else "na"
    # ConsistencyScore is the documented SOLE ranking key (scoring.py); until 2026-08-15 nothing
    # in the production path ever wrote it, so lab ranking degenerated to unranked and reseed
    # scored every survivor 0.0 (latent bug found in the 2026-07-26 audit, roadmap #3).
    score = _scoring.consistency_score(report, None, generations=0, m=m, proving=True)
    return StrategyRecord(
        id=fp, strategy=strategy, fingerprint=fp, canon_bucket=cb, behavioral_cohort=None,
        archetype=arch, generation=0, cohort=f"{arch}:{bucket}", lineage=None,
        status="proving", created_at_iso=as_of, as_of=as_of, gate_a=report,
        score=score, fail_codes=[], origin="lab_generated")


def ingest_proposals(library: list[StrategyRecord], candidates, as_of: str, *,
                     cfg: LabConfig, gate_cfg: GateAConfig, cost: CostModel,
                     get_bars, m: int, lockbox_loader=None,
                     max_admit: int | None = None) -> IngestResult:
    lib_fps = {r.fingerprint for r in library}
    canon_buckets = {r.canon_bucket for r in library}
    blocked_fps = {r.fingerprint for r in library
                   if r.status in _KILLED and _reseed.cooldown_active(r, as_of)}
    revivable_fps = {r.fingerprint for r in library
                     if r.status in _KILLED and not _reseed.cooldown_active(r, as_of)}

    bars_by_symbol, _ = fetch_basket_bars(get_bars, count=gate_cfg.min_lookback_bars + 300,
                                          min_ok_frac=cfg.basket_min_ok_frac)
    lockbox = lockbox_loader() if lockbox_loader else None

    accepted: list[str] = []
    failed: list[dict] = []
    duplicates: list[str] = []
    seen: set[str] = set()
    m_now = m

    for cand in candidates[: cfg.max_candidates_per_batch]:
        fp = dedup.fingerprint(cand)
        cb = dedup.canon_bucket(cand, period_bucket=cfg.canon_period_bucket)
        if fp in seen or fp in blocked_fps:
            duplicates.append(fp)
            continue
        is_dup = (fp in lib_fps or cb in canon_buckets) and fp not in revivable_fps
        if is_dup:
            duplicates.append(fp)
            continue
        seen.add(fp)
        m_now += 1                                  # +1 per candidate Gate-A campaign (incl. revivals)
        report = gate_a.screen_one(cand, bars_by_symbol, cfg=gate_cfg, cost=cost, m=m_now,
                                   lockbox_bars_by_symbol=lockbox)
        if report.passed:
            rec = _new_record(cand, fp, cb, as_of, report, m=m_now)
            _upsert(library, rec)
            accepted.append(fp)
            lib_fps.add(fp)
            canon_buckets.add(cb)
            if max_admit is not None and len(accepted) >= max_admit:
                break          # open-slot budget filled; stop screening (caps admits AND M)
        else:
            # Raw metric values ride along with the fail codes (decision-grade telemetry,
            # 2026-08-15) so a later review can tell a hair's-width near-miss from a mile-off
            # reject. Raw values, not pass/fail gaps — they stay meaningful if floors change.
            failed.append({"fingerprint": fp, "fail_codes": list(report.fail_codes),
                           "dsr": round(report.dsr, 6),
                           "ci_lb": round(report.expectancy_ci[0], 6),
                           "net_edge": round(report.net_edge, 6),
                           "edge_floor": round(report.edge_floor, 6),
                           "breadth_frac": round(report.breadth_frac, 4),
                           "pooled_trades": report.pooled_trades,
                           "max_drawdown_pct": round(report.max_drawdown_pct, 4)})
    return IngestResult(accepted=accepted, gate_a_failed=failed, duplicates=duplicates,
                        m_after=m_now)


def advance(library: list[StrategyRecord], *, cfg: LabConfig, gate_a: GateAConfig,
            gate_b: GateBConfig, cost: CostModel, get_bars, today_et: str, now_iso: str,
            load_state, save_state, m: int) -> AdvanceResult:
    """Advance each proving record by one epoch (capped at cfg.max_concurrent_proving).

    `gate_a`/`gate_b` are GateAConfig/GateBConfig params (the module-level `gate_a` alias is
    shadowed inside this function; that is intentional — advance never calls screen_one).
    """
    proving = [r for r in library if r.status == "proving" and r.trial_id]
    proving = proving[: cfg.max_concurrent_proving]

    fetch_count = max(gate_b.fetch_floor, gate_a.min_lookback_bars)
    fresh_by_symbol, _ = fetch_basket_bars(get_bars, count=fetch_count,
                                           min_ok_frac=cfg.basket_min_ok_frac)

    advanced: list[str] = []
    promoted: list[str] = []
    killed: list[str] = []
    any_bar = False

    for rec in proving:
        state = load_state(rec.trial_id)
        new_state, n = _trial.advance(state, fresh_by_symbol, now_iso=now_iso,
                                      today_et=today_et, cost=cost)
        if n > 0:
            any_bar = True
            advanced.append(rec.id)
        verdict = _verdict.evaluate_verdict(new_state, cfg_a=gate_a, cfg_b=gate_b, m=m)
        new_state.status = verdict
        rec.status = verdict
        # Refresh the ranking key with the LIVE trial book (trial-aware DSR) each epoch; a
        # promoted survivor sheds the proving cap the moment its verdict says so.
        try:
            rec.score = _scoring.consistency_score(
                rec.gate_a, new_state.book, generations=rec.generation, m=m,
                proving=(verdict == "proving"))
        except Exception:
            pass  # ranking must never kill an advance epoch
        if verdict in ("provisional_proven", "confirmed_proven"):
            promoted.append(rec.id)
        elif verdict in ("rejected", "retired"):
            killed.append(rec.id)
            rec.kills += 1
            cd = _reseed.next_cooldown(rec.kills, cfg=cfg)
            if cd is None:
                rec.status = new_state.status = "retired"
                rec.cooldown_until_iso = None
            else:
                rec.cooldown_until_iso = _add_days(now_iso, cd)
        save_state(new_state)

    return AdvanceResult(advanced=advanced, promoted=promoted, killed=killed, no_op=not any_bar)


def reseed(library: list[StrategyRecord], as_of: str, *, cfg: LabConfig,
           regime_now: RegimeTag) -> ReseedResult:
    """Delegate to _reseed.select_seeds (Gate-B survivors only as seeds)."""
    return _reseed.select_seeds(library, as_of, cfg=cfg, regime_now=regime_now)


_PROVEN = ("provisional_proven", "confirmed_proven")


def _archetype_aggregates(library: list[StrategyRecord]) -> list[ArchetypeAggregate]:
    out: list[ArchetypeAggregate] = []
    for arch in sorted({r.archetype for r in library}):
        recs = [r for r in library if r.archetype == arch]
        out.append(ArchetypeAggregate(
            archetype=arch,
            lifetime_proposed=len(recs),
            lifetime_proving=sum(1 for r in recs if r.status == "proving"),
            lifetime_proven=sum(1 for r in recs if r.status in _PROVEN),
            lifetime_killed=sum(1 for r in recs if r.status in _KILLED)))
    return out


def _shaped_feedback(library: list[StrategyRecord], tombstones: list[dict]) -> ShapedFeedback:
    fold_pass = fold_fail = 0
    for r in library:
        if r.gate_a:
            for f in r.gate_a.folds:
                if f.expectancy > 0:
                    fold_pass += 1
                else:
                    fold_fail += 1
    leaf_counter: Counter = Counter()
    for r in library:
        if r.status in _KILLED:
            for leaf in archetype._leaves(r.strategy.entry):
                leaf_counter[leaf.type] += 1
    most_failing = leaf_counter.most_common(1)[0][0] if leaf_counter else None
    codes: Counter = Counter()
    for t in tombstones:
        for c in t.get("fail_codes", []):
            codes[c] += 1
    return ShapedFeedback(fold_pass_count=fold_pass, fold_fail_count=fold_fail,
                          most_failing_condition=most_failing,
                          common_fail_codes=[c for c, _ in codes.most_common(5)])


def build_proposal_brief(library: list[StrategyRecord], as_of: str, *, regime_now: RegimeTag,
                         reseed_result: ReseedResult, cfg: LabConfig,
                         tombstones: list[dict]) -> ProposalBrief:
    avoid = sorted({r.fingerprint for r in library}
                   | {t["fingerprint"] for t in tombstones if "fingerprint" in t})
    patterns = sorted({c for t in tombstones for c in t.get("fail_codes", [])})
    return ProposalBrief(
        as_of=as_of,
        regime_now=regime_now,
        universe=list(LAB_BASKET),
        objective=OBJECTIVE_TEXT,
        archetype_stats=_archetype_aggregates(library),
        seeds=reseed_result.seeds,
        explore_quota=reseed_result.explore_quota,
        avoid_fingerprints=avoid,
        avoid_canon_buckets=sorted({r.canon_bucket for r in library}),
        tombstone_patterns=patterns,
        feedback=_shaped_feedback(library, tombstones))
