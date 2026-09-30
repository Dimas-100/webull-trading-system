"""Impure composition for the Strategy Learning Machine surface.

The ONLY lab module that does I/O: fetches bars (market_data), runs the pure orchestrator
(webull_api.lab), and persists via lab_store. Read-only views + token-free actions for the
routes and the webull-lab MCP. NO order path — never imports webull_api.trading.
"""
from __future__ import annotations

import logging

import dataclasses
import hashlib
import json
import os
import random
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from webull_api import market_data
from webull_api.strategy.bars import TIMEFRAME_TO_TIMESPAN, to_ohlcv
from webull_api.tiingo import bars as tiingo_bars
from webull_api.strategy.schema import Strategy
from webull_api.lab.schema import (
    DEFAULT_COST_CONFIG, DEFAULT_GATE_A_CONFIG, DEFAULT_GATE_B_CONFIG, DEFAULT_LAB_CONFIG, LAB_BASKET,
    BatchDecision, CycleReport, RegimeTag, StagnationState,
)
from webull_api.lab import cycle
from webull_api.lab import gate_a as gate_a_engine
from webull_api.lab import lab_analytics
from webull_api.lab import orchestrator as orch
from webull_api.lab import preflight as lab_preflight
from webull_api.lab import regime as regime_engine
from webull_api.lab import trial as trial_engine
from webull_api.lab import verdict as verdict_engine

from . import lab_store

LOCK = threading.Lock()                 # serialize the in-process web-button cycle
_monotonic = time.monotonic             # patchable seam: the network stack shares time.monotonic
_CYCLE_LOCK_STALE_SEC = 15 * 60



_log = logging.getLogger(__name__)

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_et() -> str:
    return datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")


def _cost():
    return gate_a_engine.stress_cost(DEFAULT_COST_CONFIG)


def _bar_source() -> str:
    src = (os.environ.get("WEBULL_LAB_BAR_SOURCE") or "webull").strip().lower()
    if src not in ("webull", "tiingo"):
        _log.warning("lab: WEBULL_LAB_BAR_SOURCE=%r unknown — using webull", src)
        return "webull"
    return src


_LOOKBACK_CEILING = 20_000     # M9: a value this large can't come from a real basket's history


def _lookback_bars() -> int:
    """Depth only goes up, and only so far: below the gate's own default (or unparseable) → the
    default; above the sanity ceiling (M9 — no real basket symbol has this much daily history) →
    also the default."""
    base = DEFAULT_GATE_A_CONFIG.min_lookback_bars
    raw = os.environ.get("WEBULL_LAB_LOOKBACK_BARS", "")
    try:
        n = int(raw)
    except ValueError:
        return base
    if n < base:
        _log.warning("lab: WEBULL_LAB_LOOKBACK_BARS=%s below the default %s — ignored", n, base)
        return base
    if n > _LOOKBACK_CEILING:
        _log.warning("lab: WEBULL_LAB_LOOKBACK_BARS=%s above the ceiling %s — ignored", n, _LOOKBACK_CEILING)
        return base
    return n


def gate_a_config():
    """The gate's defaults, byte-untouched, with the shell's lookback laid over (spec §2).
    GateAConfig is a frozen dataclass (not pydantic) — dataclasses.replace is its model_copy.
    Every bar-fetching call site must route through this (or `_lookback_bars()` directly) so
    depth stays consistent across triggers: `run_cycle`, `advance_all`, `propose`, `submit`."""
    n = _lookback_bars()
    return DEFAULT_GATE_A_CONFIG if n == DEFAULT_GATE_A_CONFIG.min_lookback_bars \
        else dataclasses.replace(DEFAULT_GATE_A_CONFIG, min_lookback_bars=n)


def _get_bars(symbol: str, timeframe: str = "1D", count: int = DEFAULT_GATE_A_CONFIG.min_lookback_bars):
    """(symbol, strategy-timeframe, count) -> OHLCV bars. Source per WEBULL_LAB_BAR_SOURCE:
    webull (default) or the offline Tiingo store. MarketDataNotEntitled propagates on webull."""
    ts = TIMEFRAME_TO_TIMESPAN.get(timeframe, "D")
    if _bar_source() == "tiingo":
        return tiingo_bars.make_get_bars()(symbol, ts, count=str(count))
    return to_ohlcv(market_data.get_bars(symbol, ts, count=str(count)))


# ── read-only views ───────────────────────────────────────────────────────────

def regime_now(*, get_bars=None):
    """Default get_bars routes through `_get_bars` (webull vs tiingo per WEBULL_LAB_BAR_SOURCE,
    2026-09-07 tiingo depth C2); an explicitly passed `get_bars` is called directly, unchanged.
    `to_ohlcv` is idempotent, so wrapping an already-normalized source stays correct."""
    if get_bars is None:
        raw = _get_bars(LAB_BASKET[0], "1D", _lookback_bars())
    else:
        raw = get_bars(LAB_BASKET[0], "D", count=str(_lookback_bars()))
    return regime_engine.compute_regime(to_ohlcv(raw))


def library_view(status: str = "", limit: int = 20) -> list[dict]:
    lib = lab_store.load_library()
    if status:
        lib = [r for r in lib if r.status == status]
    lib.sort(key=lambda r: -(r.score.score if r.score else -1e18))   # ConsistencyScore is the sole rank key
    if limit and limit > 0:
        lib = lib[:limit]
    return [r.model_dump() for r in lib]


def candidate_detail(rec_id: str) -> dict:
    rec = lab_store.get_record(rec_id)                # raises FileNotFoundError -> route 404
    out = rec.model_dump()
    out["trial"] = None
    if rec.trial_id:
        try:
            out["trial"] = lab_store.load_trial(rec.trial_id).model_dump()
        except FileNotFoundError:
            out["trial"] = None
    return out


def lab_status() -> dict:
    lib = lab_store.load_library()
    counts: dict[str, int] = {}
    for r in lib:
        counts[r.status] = counts.get(r.status, 0) + 1
    meta = lab_store.read_meta()
    summary = lab_analytics.build_lab_summary(lib, lab_store.load_all_books(),
                                              generated_at_iso=_now_iso())
    return {"counts": counts, "M": meta.get("M", 0),
            "last_advance": meta.get("last_advance_date"), "summary": summary.model_dump(),
            "last_cycle": meta.get("last_cycle"), "cycle_seq": meta.get("cycle_seq", 0),
            "stagnation": meta.get("stagnation", {}),
            "bar_source": _bar_source(), "lookback_bars": _lookback_bars()}


def proven_view() -> list[dict]:
    """Proven records, each enriched with its LIVE-vs-proof comparison (loop seam #2, slice 2b):
    the strategy's live attributed-paper stats next to its Gate-B proof + a holding/decayed read.
    Read-only; a journal-read failure degrades to `live=None` (no live data)."""
    from webull_api.journal import pairing

    from . import journal_store
    proven = lab_store.load_proven()
    try:
        closed, _ = pairing.pair_fills(journal_store.load_fills())
        by_tid = {row["trial_id"]: row for row in lab_analytics.live_vs_proven_rows(proven, closed)}
    except Exception:
        by_tid = {}
    out = []
    for p in proven:
        d = p.model_dump()
        d["live"] = by_tid.get(str(p.gate_b.get("trial_id") or p.strategy.name))
        out.append(d)
    return out


def funnel_view(recent: int = 12) -> dict:
    """Gate-A funnel + fail-code aggregates (read-only, disk-only — no market data, no orders).
    Lifetime aggregates need every cycle, so read the whole (small, append-only) cycles.jsonl;
    an unreadable proven shelf degrades to proven=0 rather than failing the whole payload."""
    cycles = lab_store.read_cycles(limit=100_000)
    try:
        proven_total = len(lab_store.load_proven())
    except Exception:
        proven_total = 0
    return lab_analytics.funnel_summary(cycles, proven_total=proven_total,
                                        recent=max(int(recent), 1))


def brief():
    lib = lab_store.load_library()
    rn = regime_now()
    rr = orch.reseed(lib, _now_iso(), cfg=DEFAULT_LAB_CONFIG, regime_now=rn)
    return orch.build_proposal_brief(lib, _now_iso(), regime_now=rn, reseed_result=rr,
                                     cfg=DEFAULT_LAB_CONFIG, tombstones=lab_store.read_tombstones())


# ── token-free actions ──────────────────────────────────────────────────────────

def propose(candidates: list[Strategy], notes: str = "") -> dict:
    lib = lab_store.load_library()
    meta = lab_store.read_meta()
    res = orch.ingest_proposals(lib, candidates, _now_iso(), cfg=DEFAULT_LAB_CONFIG,
                                gate_cfg=gate_a_config(), cost=_cost(),
                                get_bars=_get_bars, m=meta.get("M", 0))
    lab_store.save_library(lib)                       # ingest_proposals appended in place
    lab_store.write_meta({**meta, "M": res.m_after})
    for f in res.gate_a_failed:
        lab_store.append_event({"type": "gate_a_failed", "at": _now_iso(), **f})
    return res.model_dump()


def submit(candidate_ids: list[str]) -> dict:
    submitted, skipped = [], []
    meta = lab_store.read_meta()
    for rid in candidate_ids:
        rec = lab_store.get_record(rid)
        if not (rec.gate_a and rec.gate_a.passed):    # re-check the Gate-A guard
            skipped.append({"id": rid, "reason": "gate_a not passed"})
            continue
        bars = {s: _get_bars(s, rec.strategy.timeframe, count=_lookback_bars()) for s in LAB_BASKET}
        state = trial_engine.open_trial(rec.strategy, bars, trial_id=rid, now_iso=_now_iso(),
                                        inception_et_date=_today_et(), gate_a=rec.gate_a,
                                        m=meta.get("M", 0))   # freeze the forward-DSR penalty at open
        lab_store.save_trial(state)
        rec.trial_id = rid
        rec.status = "proving"
        lab_store.upsert_record(rec)
        submitted.append(rid)
    return {"submitted": submitted, "skipped": skipped}


def advance_all(symbols: str = "", max_trials: int = 0):
    lib = lab_store.load_library()
    meta = lab_store.read_meta()
    res = orch.advance(lib, cfg=DEFAULT_LAB_CONFIG, gate_a=gate_a_config(),
                       gate_b=DEFAULT_GATE_B_CONFIG, cost=_cost(), get_bars=_get_bars,
                       today_et=_today_et(), now_iso=_now_iso(), load_state=lab_store.load_trial,
                       save_state=lab_store.save_trial, m=meta.get("M", 0))
    lab_store.save_library(lib)                       # advance updated statuses in place
    lab_store.write_meta({**meta, "last_advance_date": _today_et()})
    for rid in res.promoted:
        lab_store.append_event({"type": "promote", "id": rid, "at": _now_iso()})
    for rid in res.killed:
        lab_store.append_event({"type": "kill", "id": rid, "at": _now_iso()})
    return res


def promote(ids: list[str]) -> dict:
    promoted, refused = [], []
    meta = lab_store.read_meta()
    for rid in ids:
        rec = lab_store.get_record(rid)
        if rec.status != "confirmed_proven":          # verdict gate — the human gate lives downstream
            refused.append({"id": rid, "status": rec.status, "reason": "not confirmed_proven"})
            continue
        state = lab_store.load_trial(rec.trial_id)
        rec_proven = verdict_engine.build_proven_record(rec, state, graduated_at_iso=_now_iso(),
                                                        m_at_graduation=meta.get("M", 0))
        lab_store.save_proven(rec_proven)
        lab_store.append_event({"type": "promote", "id": rid, "at": _now_iso()})
        promoted.append(rid)
    return {"promoted": promoted, "refused": refused}


def reject(ids: list[str], reason: str = "") -> dict:
    rejected = []
    for rid in ids:
        rec = lab_store.get_record(rid)
        rec.status = "rejected"
        lab_store.upsert_record(rec)
        lab_store.append_event({"type": "reject", "id": rid, "reason": reason, "at": _now_iso()})
        rejected.append(rid)
    return {"rejected": rejected}


# ── unattended cycle (impure shell around the pure cycle.run_cycle) ───────────────

def _lock_path() -> Path:
    return lab_store.lab_dir() / ".cycle.lock"


def _acquire_cycle_lock() -> bool:
    """Write <LAB_DIR>/.cycle.lock (pid+mtime). A fresh lock (< ~15 min) ⇒ a cycle is already
    running → refuse. Called INSIDE the in-process LOCK so the same-day/M check is atomic."""
    p = _lock_path()
    try:
        if p.exists() and (time.time() - p.stat().st_mtime) < _CYCLE_LOCK_STALE_SEC:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pid": os.getpid(), "at": _now_iso()}), encoding="utf-8")
        return True
    except OSError:
        return False


def _release_cycle_lock() -> None:
    try:
        _lock_path().unlink()
    except OSError:
        pass


def _trial_files_exist() -> bool:
    d = lab_store.lab_dir() / "trials"
    return d.exists() and any(d.glob("*.json"))


def _memo_get_bars(failed: set | None = None):
    """get_bars(symbol, timeframe, count) that fetches each (symbol, timeframe) at most once per
    cycle (at the largest count any consumer needs) and slices to the requested count. Same call
    contract advance_all/propose inject via _get_bars. When `failed` is given, a symbol whose fetch
    raises (other than an entitlement error, which propagates as a hard abort) is recorded there for
    observability — the pure engine already TOLERATES the drop; this just surfaces WHICH symbols
    degraded so run_cycle can note them on the report."""
    cache: dict[tuple[str, str], tuple[int, list]] = {}

    def getter(symbol: str, timeframe: str = "1D",
               count: int = DEFAULT_GATE_A_CONFIG.min_lookback_bars):
        n = int(count)
        key = (symbol, timeframe)
        have = cache.get(key)
        if have is None or have[0] < n:
            try:
                cache[key] = (n, list(_get_bars(symbol, timeframe, n)))
            except market_data.MarketDataNotEntitledError:
                raise
            except Exception:
                if failed is not None:
                    failed.add(symbol)
                raise
        bars = cache[key][1]
        return bars[-n:] if len(bars) > n else bars

    return getter


def _zero_decision() -> BatchDecision:
    return BatchDecision(active_trials=0, admitted_waiting=0, open_slots=0, pass_rate_est=0.0,
                         mutation_n=0, explore_n=0, max_admit=0)


def _best_effort_regime() -> RegimeTag:
    try:
        return regime_now()
    except Exception:
        return RegimeTag(trend="side", vol="low")


def _noop_report(errors: list[str]) -> CycleReport:
    meta = lab_store.read_meta()
    m = meta.get("M", 0)
    return CycleReport(cycle_seq=int(meta.get("cycle_seq", 0)), cycle_date=_today_et(),
                       ran_at_iso=_now_iso(), regime=_best_effort_regime(), no_op=True, no_bar=True,
                       decision=_zero_decision(), m_before=m, m_after=m,
                       stagnation=StagnationState.model_validate(meta.get("stagnation", {}) or {}),
                       slots_cap=DEFAULT_LAB_CONFIG.max_concurrent_proving, errors=errors)


def _advance_only_heartbeat(meta: dict) -> CycleReport:
    """Same-day re-trigger: run ONLY the existing advance path (M-frugal), summarize it."""
    adv = advance_all()
    m = meta.get("M", 0)
    return CycleReport(cycle_seq=int(meta.get("cycle_seq", 0)), cycle_date=_today_et(),
                       ran_at_iso=_now_iso(), regime=_best_effort_regime(), no_op=True,
                       no_bar=adv.no_op, advanced=adv.advanced, promoted=adv.promoted,
                       killed=adv.killed, decision=_zero_decision(), m_before=m, m_after=m,
                       stagnation=StagnationState.model_validate(meta.get("stagnation", {}) or {}),
                       slots_cap=DEFAULT_LAB_CONFIG.max_concurrent_proving)


def _promote_confirmed_to_shelf(lib, m_at: int) -> None:
    """Shell-side shelf I/O (the pure cycle only sets status='confirmed_proven'): write a
    PaperProvenRecord (promoted_by='autopilot') for each confirmed record lacking a proven file."""
    existing = {str(r.gate_b.get("trial_id") or r.strategy.name) for r in lab_store.load_proven()}
    for rec in lib:
        if rec.status != "confirmed_proven" or not rec.trial_id:
            continue
        if rec.trial_id in existing or rec.strategy.name in existing:
            continue
        try:
            state = lab_store.load_trial(rec.trial_id)
        except FileNotFoundError:
            continue
        rec_proven = verdict_engine.build_proven_record(rec, state, graduated_at_iso=_now_iso(),
                                                        m_at_graduation=m_at, promoted_by="autopilot")
        lab_store.save_proven(rec_proven)
        lab_store.append_event({"type": "promote", "id": rec.trial_id, "at": _now_iso(),
                                "promoted_by": "autopilot"})


def run_cycle(force: bool = False) -> CycleReport:
    """Impure one-cycle wrapper around the pure cycle.run_cycle. Mirrors advance_all — the ONLY I/O
    composition. Cross-process lock + same-day guard + cold-start refusal + (today_et, M)-seeded rng
    + crash-safe persist order (meta(M↑) FIRST → library → proven → events → cycles). NO order path."""
    with LOCK:
        if not _acquire_cycle_lock():
            return _noop_report(errors=["cycle already running"])
        try:
            meta = lab_store.read_meta()                       # RE-READ inside the critical section
            lib = lab_store.load_library()
            if not lib and _trial_files_exist():
                # Genuine lost data: a trial file exists but its library record is gone (library.json
                # was lost) — refuse so we don't orphan the open trial. An empty library with M>0 but
                # NO trials is a LEGITIMATE state (interactive proposing bumped M, nothing passed
                # Gate-A yet), so the autopilot proceeds and keeps searching; M is preserved in meta,
                # so there is no double-count.
                return _noop_report(errors=[
                    f"library empty but trial files exist (M={meta.get('M')}) — library.json lost; "
                    f"refusing to cold-start (would orphan the open trials)"])
            if meta.get("last_cycle_date") == _today_et() and not force:
                return _advance_only_heartbeat(meta)
            gate_cfg = gate_a_config()                          # env-selected lookback, gate defaults untouched
            degraded: set[str] = set()
            bars = _memo_get_bars(degraded)
            # Bar-sanity preflight (2026-08-16): refuse to screen a suspect feed — the memoized
            # getter makes this fetch free for the cycle's own regime/advance reads at the same
            # count, and a refusal costs one evening while corrupted screening cost 31 cycles.
            pf_bars, _pf_dropped = orch.fetch_basket_bars(
                bars, count=gate_cfg.min_lookback_bars,
                min_ok_frac=DEFAULT_LAB_CONFIG.basket_min_ok_frac)
            problems = lab_preflight.check_bars(pf_bars, today_et=_today_et())
            if problems:
                shown = "; ".join(problems[:8]) + (
                    f" (+{len(problems) - 8} more)" if len(problems) > 8 else "")
                return _noop_report(errors=[f"bar preflight failed — cycle refused: {shown}"])
            # Lookback-vs-history shell check (C1, 2026-09-07 tiingo depth fix wave): Gate A fails
            # the WHOLE candidate on one basket symbol shorter than the configured lookback, so
            # refuse here — loudly, before any M is spent — rather than let every candidate quietly
            # fail Gate A for a reason that has nothing to do with the strategy.
            short = lab_preflight.short_history(pf_bars, min_bars=gate_cfg.min_lookback_bars)
            if short:
                names = ", ".join(f"{sym}({n})" for sym, n in short)
                return _noop_report(errors=[
                    f"lookback {gate_cfg.min_lookback_bars} exceeds history for: {names} — lower "
                    "WEBULL_LAB_LOOKBACK_BARS or drop the symbol; cycle refused, M unchanged"])
            # No bar dated today (weekend / NYSE holiday): nothing new to screen against, so no
            # candidates are generated and M does not move (2026-09-07: Labor Day had cost 64
            # trials). Not an error — a quiet no-op row; the task still fires, the day is skipped.
            if not lab_preflight.fresh_bar_day(pf_bars, today_et=_today_et()):
                _log.info("lab: no fresh daily bar for %s — market closed? no candidates generated, M unchanged",
                          _today_et())
                return _noop_report(errors=[])
            rng = random.Random(int.from_bytes(
                hashlib.sha256(f"{_today_et()}|{meta['M']}".encode()).digest()[:8], "big"))
            stag = StagnationState.model_validate(meta.get("stagnation", {}) or {})
            t0 = _monotonic()              # the pure core is time-free; the shell owns elapsed_ms
            lib2, report = cycle.run_cycle(
                lib, cfg=DEFAULT_LAB_CONFIG, gate_a_cfg=gate_cfg, cost=_cost(), get_bars=bars,
                load_state=lab_store.load_trial, save_state=lab_store.save_trial,
                load_books=lab_store.load_all_books, today_et=_today_et(), now_iso=_now_iso(),
                m=meta["M"], tombstones=lab_store.read_tombstones(), stagnation=stag, rng=rng)
            report.elapsed_ms = int((_monotonic() - t0) * 1000)
            if degraded:                                    # one flaky symbol didn't abort the cycle
                report.errors = list(report.errors) + [
                    f"market data degraded — dropped {', '.join(sorted(degraded))}"]
            report.cycle_seq = int(meta.get("cycle_seq", 0)) + 1
            # ── PERSIST ORDER (crash-safe, M-conservative): meta(M↑) FIRST ──
            lab_store.write_meta({**meta, "M": report.m_after, "last_advance_date": _today_et(),
                                  "last_cycle_date": _today_et(), "cycle_seq": report.cycle_seq,
                                  "last_cycle": report.model_dump(),
                                  "stagnation": report.stagnation.model_dump()})
            lab_store.save_library(lib2)
            _promote_confirmed_to_shelf(lib2, m_at=report.m_after)
            for ev in report.events:
                lab_store.append_event(ev)
            lab_store.append_cycle(report)
            return report
        finally:
            _release_cycle_lock()


def cycles_view(limit: int = 20) -> dict:
    return {"last": lab_store.read_meta().get("last_cycle"),
            "history": lab_store.read_cycles(limit)}


def _days_since_cycle(last: str, today: str) -> int | None:
    if not last:
        return None
    try:
        return (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
    except ValueError:
        return None


def _cycle_summary(c: dict) -> dict:
    return {"cycle_seq": c.get("cycle_seq"), "date": c.get("cycle_date"),
            "generated": c.get("generated", 0), "accepted": len(c.get("accepted", []) or []),
            "proving_active": c.get("proving_active", 0), "slots_cap": c.get("slots_cap", 0),
            "m_before": c.get("m_before", 0), "m_after": c.get("m_after", 0),
            "regime": c.get("regime"), "promoted": len(c.get("promoted", []) or []),
            "killed": len(c.get("killed", []) or []), "errors": list(c.get("errors", []) or [])}


def _record_summary(r) -> dict:
    ga = r.gate_a
    return {"id": r.id, "name": r.strategy.name, "status": r.status, "archetype": r.archetype,
            "gate_a_dsr": getattr(ga, "dsr", None) if ga else None}


def status_view(num_cycles: int = 12) -> dict:
    """Read-only one-glance progress snapshot: total strategies tested (M), cycles run + last-cycle
    date + a staleness flag, the recent cycle history, the library by status (+ active candidates),
    and the proven shelf. Backs the webull-lab `lab_status` MCP tool. No market data, no orders."""
    from collections import Counter
    meta = lab_store.read_meta()
    cycles = lab_store.read_cycles(limit=max(int(num_cycles), 1))
    library = lab_store.load_library()
    proven = lab_store.load_proven()
    last = meta.get("last_cycle_date", "") or ""
    today = _today_et()
    gap = _days_since_cycle(last, today)
    live = [r for r in library if r.status in ("proving", "provisional_proven", "confirmed_proven")]
    return {
        "M": meta.get("M", 0),
        "cycles_run": meta.get("cycle_seq", 0),
        "last_cycle_date": last,
        "ran_today": bool(last) and last == today,
        "days_since_last_cycle": gap,
        "stale": gap is not None and gap > 4,
        "recent_cycles": [_cycle_summary(c) for c in cycles],
        "library": {"total": len(library),
                    "by_status": dict(Counter(r.status for r in library)),
                    "active": [_record_summary(r) for r in live[:20]]},
        "proven_shelf": {"total": len(proven),
                         "records": [{"name": p.strategy.name, "graduated_at": p.graduated_at,
                                      "promoted_by": p.promoted_by} for p in proven[:20]]},
    }
