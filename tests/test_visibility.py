"""Visibility is DERIVED from the track registry and the scheduler (spec 2026-09-14 cockpit cleanup §1).
Pure rules, fed hand-made tracks/rows so the tests never depend on today's registry."""
from types import SimpleNamespace as NS

from webull_web import visibility as v
from webull_web.routine import RoutineRow
from webull_web.tracks import PLAN_TABS, TRACKS, tab_states


def cell(key, money, state, note=""):
    return NS(key=key, money=money, state=state, state_note=note)


TRACKS_FAKE = (
    cell("swing_rsi2", "paper", "running", "17:30 suite"),
    cell("swing_rsi2", "real", "running", "autopilot exits"),
    cell("swing_pullback", "real", "running", "drafts"),
    cell("day_orb", "paper", "paused", "paper day session CANCELLED 2026-09-14 (owner)"),
    cell("day_orb", "real", "paused", "real day trades PAUSED 2026-09-08 (owner)"),
    cell("lab", "none", "running", "bench feeder"),
    cell("options_paper", "paper", "paused", "entries paused 2026-08-31"),
    cell("swing_ibs_etf", "paper", "running", "sandbox runner"),
)


def test_visible_tabs_are_the_running_plan_tabs_in_registry_order():
    states = {"swing_pullback": {"state": "running", "note": ""}, "swing_rsi2": {"state": "running", "note": ""},
              "day_orb": {"state": "running", "note": ""}}                # not a plan tab since 2026-09-29
    assert v.visible_tabs(states) == ["swing_rsi2", "swing_pullback"]
    # a tab the registry says nothing about is HIDDEN — fail toward less, never toward a phantom tab
    assert v.visible_tabs({"swing_rsi2": {"state": "running", "note": ""}}) == ["swing_rsi2"]
    # the default reads the real registry and stays in PLAN_TABS order
    assert v.visible_tabs() == [k for k in PLAN_TABS if tab_states()[k]["state"] == "running"]


def test_paused_plans_lists_keys_where_no_cell_runs_with_each_dated_note():
    got = v.paused_plans(TRACKS_FAKE)
    assert [p["key"] for p in got] == ["day_orb", "options_paper"]
    assert got[0]["label"] == "day_orb"          # its label went with the archived track (2026-09-29)
    assert got[0]["notes"] == ["paper: paper day session CANCELLED 2026-09-14 (owner)",
                               "real: real day trades PAUSED 2026-09-08 (owner)"]
    assert got[1]["notes"] == ["paper: entries paused 2026-08-31"]
    # a plan with ONE running cell is not paused
    assert "swing_rsi2" not in [p["key"] for p in got]


def test_plan_cell_visible_needs_a_running_cell_that_holds_money():
    assert v.plan_cell_visible("swing_rsi2", "real", TRACKS_FAKE) is True
    assert v.plan_cell_visible("swing_rsi2", "paper", TRACKS_FAKE) is True
    assert v.plan_cell_visible("day_orb", "paper", TRACKS_FAKE) is False       # paused
    assert v.plan_cell_visible("swing_ibs_etf", "real", TRACKS_FAKE) is False  # no such cell (not funded)
    assert v.plan_cell_visible("lab", "none", TRACKS_FAKE) is False            # no money
    assert v.plan_cell_visible("nope", "real", TRACKS_FAKE) is False


def test_routine_paused_keys_are_the_paused_task_rows_run_log_keys():
    rows = (RoutineRow("09:25", "day_orb", "paper day", "task", evidence="paper_day", paused="cancelled"),
            RoutineRow("19:05", "lab", "lab cycle", "task", evidence="jsonl:data/lab/cycles.jsonl", paused="paused"),
            RoutineRow("17:30", "swing_rsi2", "suite", "task", evidence="note"),
            RoutineRow("09:44", "day_orb", "screen", "owner", evidence="file:x-{date}.log", paused="paused"))
    assert v.routine_paused_keys(rows) == frozenset({"paper_day"})
    assert v.routine_paused_keys() == frozenset()          # the RSI2-only routine has no paused task rows


def test_live_jobs_exclude_disabled_tasks_paused_rows_and_expected_no_ops():
    paused = frozenset({"paper_day"})
    assert v.is_live_job({"key": "rsi2", "task_state": "Ready"}, paused) is True
    assert v.is_live_job({"key": "lab_cycle", "task_state": "Disabled"}, paused) is False
    assert v.is_live_job({"key": "paper_day", "task_state": "Ready"}, paused) is False
    assert v.is_live_job({"key": "options_entry", "task_state": "Ready"}, paused) is False
    assert v.is_live_job({"key": "proven"}, paused) is False
    # scheduler unreadable (no task_state) -> still live; never assert "disabled" from silence
    assert v.is_live_job({"key": "netliq"}, paused) is True
    for key in v.EXPECTED_NO_OP:
        assert v.EXPECTED_NO_OP[key]                       # every expected no-op carries its reason


def test_problem_of_flags_bad_statuses_and_unexpected_skips_only():
    assert v.problem_of({"key": "rsi2", "status": "ok"}) is None
    assert v.problem_of({"key": "autopilot", "status": "active"}) is None
    for s in ("stale", "error", "unknown", "never_run"):
        assert v.problem_of({"key": "x", "status": s}) == s
    assert v.problem_of({"key": "rsi2_real", "status": "ok", "last_result": "no_op",
                         "last_summary": "RSI2-real: disabled (WEBULL_RSI2_REAL_ENABLED unset)"}) == "skipped"
    assert v.problem_of({"key": "ibs_book_paper", "status": "ok", "last_result": "skipped",
                         "last_summary": "IBS book: market holiday"}) == "skipped"
    # a same-day guard no-op is the runner declining to run TWICE, not a run that did not happen
    assert v.problem_of({"key": "rsi2", "status": "ok", "last_result": "no_op",
                         "last_summary": "RSI2: already ran today"}) is None
    assert v.problem_of({"key": "rsi2", "status": "ok", "last_result": "no_op",
                         "last_summary": "RSI2: another run in progress"}) is None
    # the real-money autopilot row's own run-log result can flag it too, even when its scheduler
    # -derived status still reads "active" (finding #3: it could otherwise never become a problem row)
    assert v.problem_of({"key": "autopilot", "status": "active", "last_result": "error"}) == "error"


CADENCE = [
    {"key": "paper_day", "name": "Paper day session", "status": "ok", "next_run": "2026-09-15T09:25:00", "task_state": "Disabled"},
    {"key": "ibs_book_paper", "name": "IBS ETF book", "status": "ok", "next_run": "2026-09-15T15:56:00", "task_state": "Ready", "last_result": "ok"},
    {"key": "lab_cycle", "name": "Strategy Lab cycle", "status": "ok", "next_run": "2026-09-15T19:05:00", "task_state": "Disabled"},
    {"key": "rsi2", "name": "RSI2 (paper)", "status": "ok", "next_run": "2026-09-15T17:30:00", "task_state": "Ready", "last_result": "ok"},
    {"key": "options_entry", "name": "Options entry", "status": "ok", "next_run": "2026-09-15T17:30:00", "task_state": "Ready", "last_result": "no_op"},
    {"key": "rsi2_real", "name": "RSI2-real", "status": "ok", "next_run": "2026-09-15T17:30:00", "task_state": "Ready",
     "last_result": "no_op", "last_summary": "RSI2-real: disabled (WEBULL_RSI2_REAL_ENABLED unset)"},
    {"key": "watchdog", "name": "Suite watchdog", "status": "stale", "next_run": "2026-09-15T19:00:00", "task_state": "Ready", "last_result": "ok"},
    {"key": "autopilot", "name": "Trading autopilot (real)", "status": "active", "next_run": "2026-09-15T09:31:00", "task_state": "Ready"},
]


def test_machinery_counts_live_jobs_picks_the_next_firing_and_lists_only_problems():
    m = v.machinery(CADENCE, None, frozenset({"paper_day"}))
    assert m["live"] == 5                                   # ibs, rsi2, rsi2_real, watchdog, autopilot
    assert m["reported"] == 3                               # rsi2_real skipped + watchdog stale are problems
    assert m["next"] == {"key": "autopilot", "name": "Trading autopilot (real)", "next_run": "2026-09-15T09:31:00"}
    assert [(p["key"], p["problem"]) for p in m["problems"]] == [("rsi2_real", "skipped"), ("watchdog", "stale")]
    assert m["problems"][0]["name"] == "RSI2-real"          # the row is the cadence row, plus its reason


def test_machinery_adds_a_row_for_pool_made_divergences_or_an_unreadable_pool():
    clean = {"available": True, "divergences_today": {"skip": 2, "size": 1, "reconcile": 0, "unmapped": 0}, "last_day": "2026-09-14"}
    assert v.machinery([], clean)["problems"] == []
    dirty = {**clean, "divergences_today": {"skip": 0, "size": 0, "reconcile": 1, "unmapped": 2}}
    rows = v.machinery([], dirty)["problems"]
    assert len(rows) == 1 and rows[0]["key"] == "pool_shadow_divergence" and rows[0]["problem"] == "divergence"
    assert "3 pool-made divergence(s)" in rows[0]["note"] and rows[0]["last_run"] == "2026-09-14"
    assert v.machinery([], {"available": False, "reason": "no shadow session yet"})["problems"] == []
    rows = v.machinery([], {"unavailable": True, "detail": "boom"})["problems"]
    assert len(rows) == 1 and rows[0]["key"] == "pool_shadow_unavailable" and rows[0]["problem"] == "unavailable"
    assert "boom" in rows[0]["note"] and rows[0]["status"] == "unknown"


def test_machinery_with_nothing_live():
    assert v.machinery([]) == {"live": 0, "reported": 0, "next": None, "problems": []}


def test_the_real_registry_reads_cleanly():
    got = v.paused_plans(TRACKS)
    assert all(p["label"] and isinstance(p["notes"], list) for p in got)
    assert v.visible_tabs() and "swing_rsi2" in v.visible_tabs()
