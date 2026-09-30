"""Which plans and scheduled jobs count as live (spec 2026-09-14 cockpit cleanup §1; the web cockpit
it was built for was archived 2026-09-28 — the kestrel feed reads `is_live_job` / `problem_of`).
Visibility is DERIVED — from the track registry's running/paused state (webull_web/tracks.py) and
from the scheduler's task state on each cadence row — never configured per panel. Pure: no I/O, no
clock, no broker."""
from __future__ import annotations

from . import routine
from .tracks import MONEY_WORD, PLAN_TABS, TRACKS, label_for, tab_states

# Suite steps whose no-op is the EXPECTED state today. Each carries its reason — the reason is the
# tripwire: when one of these does something again, drop it from here and it becomes a live job.
EXPECTED_NO_OP: dict[str, str] = {
    "options_entry": "entries paused 2026-08-31 (WEBULL_OPTIONS_ENTRY_PAUSED, red-team #2)",
    "paper_eod_options": "no option positions to manage while entries are paused",
    "proven": "no proven strategies on the shelf",
}
PROBLEM_STATUSES = ("stale", "error", "unknown", "never_run")
DISABLED = "Disabled"            # the scheduler's literal state for a task the owner switched off
_SKIP_RESULTS = ("skipped", "no_op")
# A same-day guard no-op is the runner declining to run TWICE, not a run that failed to happen.
_GUARD_PHRASES = ("already ran today", "another run in progress")


def visible_tabs(states: dict | None = None) -> list[str]:
    """Plan tabs whose registry state is running, in PLAN_TABS order. A tab the registry says
    nothing about is hidden — fail toward less, never toward a phantom tab."""
    states = tab_states() if states is None else states
    return [k for k in PLAN_TABS if (states.get(k) or {}).get("state") == "running"]


def paused_plans(tracks=None) -> list[dict]:
    """Registry keys where NO cell runs, in registry order: the label plus each cell's dated note."""
    tracks = TRACKS if tracks is None else tracks
    keys: list[str] = []
    for t in tracks:
        if t.key not in keys:
            keys.append(t.key)
    out = []
    for key in keys:
        cells = [t for t in tracks if t.key == key]
        if any(t.state == "running" for t in cells):
            continue
        out.append({"key": key, "label": label_for(key),
                    "notes": [f"{MONEY_WORD[t.money]}: {t.state_note}" for t in cells if t.state_note]})
    return out


def plan_cell_visible(key: str, money: str, tracks=None) -> bool:
    """A Plans-table row exists only for a registry cell that is RUNNING and holds money."""
    tracks = TRACKS if tracks is None else tracks
    t = next((t for t in tracks if t.key == key and t.money == money), None)
    return t is not None and t.state == "running" and t.money in ("real", "paper")


def routine_paused_keys(rows=None) -> frozenset[str]:
    """Run-log keys of routine TASK rows the owner marked paused (only evidence that IS a run-log
    key — `file:`/`jsonl:` evidence names a file, not a job)."""
    rows = routine.ROUTINE if rows is None else rows
    return frozenset(r.evidence for r in rows
                     if r.kind == "task" and r.paused and r.evidence and ":" not in r.evidence)


def is_live_job(job: dict, paused_keys: frozenset[str] | None = None) -> bool:
    """A cadence row the owner should still expect to run. Scheduler silence (no task_state)
    counts as live — never assert 'disabled' from an unreadable scheduler."""
    paused_keys = routine_paused_keys() if paused_keys is None else paused_keys
    key = job.get("key")
    if key in EXPECTED_NO_OP or key in paused_keys:
        return False
    return job.get("task_state") != DISABLED


def problem_of(job: dict) -> str | None:
    """Why a live job needs the owner's eye, or None when it ran and reported."""
    status = job.get("status")
    if status in PROBLEM_STATUSES:
        return status
    if job.get("last_result") in _SKIP_RESULTS:
        summary = str(job.get("last_summary") or "")
        if not any(p in summary for p in _GUARD_PHRASES):
            return "skipped"
    # The real-money autopilot row's status comes from ap_snap (often "active"), which alone can
    # never land in PROBLEM_STATUSES -- so its OWN run-log result is the only way an errored run
    # becomes a machinery problem row (finding #3).
    if job.get("last_result") == "error":
        return "error"
    return None


def _pool_problem(pool: dict | None) -> dict | None:
    """A divergence the pool ITSELF made (reconcile / unmapped) is the phase-2 gate's blocker; the
    sizing classes (skip / size) are the pool sizing differently by construction and never a problem.
    A pool that failed to read is itself a problem — it must not be mistaken for "no shadow session yet"."""
    if not pool:
        return None
    if pool.get("unavailable"):
        return {"key": "pool_shadow_unavailable", "name": "Paper pool (shadow)", "kind": "windows_task", "schedule": "",
                "last_run": None, "next_run": None, "status": "unknown", "scheduled": True,
                "note": f"pool unavailable — {pool.get('detail') or 'unknown error'}", "last_summary": None,
                "problem": "unavailable"}
    if not pool.get("available"):
        return None
    d = pool.get("divergences_today") or {}
    reconcile, unmapped = int(d.get("reconcile") or 0), int(d.get("unmapped") or 0)
    n = reconcile + unmapped
    if n == 0:
        return None
    return {"key": "pool_shadow_divergence", "name": "Paper pool (shadow)", "kind": "windows_task", "schedule": "",
            "last_run": pool.get("last_day"), "next_run": None, "status": "error", "scheduled": True,
            "note": f"{n} pool-made divergence(s) today — reconcile {reconcile}, unmapped {unmapped} — the phase-2 gate blocker",
            "last_summary": None, "problem": "divergence"}


def machinery(cadence: list[dict], pool: dict | None = None, paused_keys: frozenset[str] | None = None) -> dict:
    """One machinery line (spec §2.3; built for the web Overview, archived 2026-09-28): how many live
    jobs reported, the next firing, and ONLY the rows that need the owner."""
    live = [j for j in cadence if is_live_job(j, paused_keys)]
    problems = [{**j, "problem": problem_of(j)} for j in live if problem_of(j)]
    pp = _pool_problem(pool)
    if pp:
        problems.append(pp)
    upcoming = [j for j in live if j.get("next_run")]
    nxt = min(upcoming, key=lambda j: str(j["next_run"])) if upcoming else None
    return {"live": len(live), "reported": len(live) - sum(1 for j in live if problem_of(j)),
            "next": {"key": nxt.get("key"), "name": nxt.get("name"), "next_run": nxt["next_run"]} if nxt else None,
            "problems": problems}
