"""The daily routine is data; status_for is pure (spec 2026-09-07 program map §4).
A fixed Tuesday, a holiday, a Friday and a last-Friday pin every status word."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from webull_web import routine

ET = ZoneInfo("America/New_York")
TUE = date(2026, 9, 8)


def at(hh, mm, d=TUE):
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=ET)


def row(key, hh, mm, result="ok", d=TUE):
    return {"key": key, "ts": at(hh, mm, d).isoformat(), "result": result}


def by_label(out, needle):
    return next(o for o in out if needle in o["label"])


def test_rows_for_weekday_friday_and_last_friday():
    tue = routine.rows_for(TUE)
    assert all(r.days == "weekdays" for r in tue)
    fri = routine.rows_for(date(2026, 9, 11))
    assert any(r.days == "friday" for r in fri) and not any(r.days == "monthly" for r in fri)
    last_fri = routine.rows_for(date(2026, 9, 25))
    assert any(r.days == "monthly" for r in last_fri)


def test_non_trading_day_is_all_off():
    out = routine.status_for(routine.ROUTINE, [], at(10, 0, date(2026, 9, 7)), lambda e, d: True)  # Labor Day
    assert out and {o["status"] for o in out} == {"off"}
    out = routine.status_for(routine.ROUTINE, [], at(10, 0, date(2026, 9, 12)), lambda e, d: True)  # Saturday
    assert {o["status"] for o in out} == {"off"}


def test_task_statuses_by_clock_and_run_log():
    rows = routine.rows_for(TUE)
    never = lambda e, d: False
    early = routine.status_for(rows, [], at(9, 0), never)
    assert by_label(early, "Suite watchdog")["status"] == "next"
    assert by_label(early, "Market open")["status"] == "next"
    due = routine.status_for(rows, [], at(9, 40), never)
    assert by_label(due, "Autopilot morning")["status"] == "due"         # 09:31 + 20
    assert by_label(due, "Market open")["status"] == "past"
    assert by_label(routine.status_for(rows, [], at(19, 10), never), "Suite watchdog")["status"] == "due"   # 19:00 + 30
    late = routine.status_for(rows, [], at(12, 0), never)
    assert by_label(late, "Autopilot morning")["status"] == "late"
    assert by_label(routine.status_for(rows, [], at(20, 0), never), "Suite watchdog")["status"] == "late"
    done = routine.status_for(rows, [row("autopilot", 9, 36)], at(12, 0), never)
    assert by_label(done, "Autopilot morning")["status"] == "done"
    # the SAME key later in the day belongs to the later row, not the morning one
    assert by_label(done, "Autopilot retry")["status"] == "next"
    wd = routine.status_for(rows, [row("watchdog", 19, 5)], at(20, 0), never)
    assert by_label(wd, "Suite watchdog")["status"] == "done"
    err = routine.status_for(rows, [row("autopilot", 9, 36, result="error")], at(12, 0), never)
    assert by_label(err, "Autopilot morning")["status"] == "error"
    skipped = routine.status_for(rows, [row("watchdog", 19, 0, result="skipped")], at(20, 0), never)
    assert by_label(skipped, "Suite watchdog")["status"] == "skipped"


def test_same_key_rows_are_split_by_time_window():
    rows = routine.rows_for(TUE)
    evening = routine.status_for(rows, [row("autopilot", 9, 36), row("autopilot", 17, 47)], at(18, 30), lambda e, d: False)
    assert by_label(evening, "Autopilot morning")["status"] == "done"
    assert by_label(evening, "Autopilot retry")["status"] == "late"
    assert by_label(evening, "Autopilot evening")["status"] == "done"


def test_owner_rows_use_the_exists_callback():
    rows = routine.rows_for(TUE)
    seen = []
    def exists(e, d):
        seen.append((e, d))
        return e.startswith("file:playbook/screens")
    out = routine.status_for(rows, [], at(10, 30), exists)
    assert by_label(out, "Evening scan")["status"] == "done"      # file evidence present -> done whatever the clock says
    assert ("file:playbook/screens/{date}-eod-screen.md", TUE) in seen
    # an owner row past its grace is "past", never "late" — your rows inform, they don't alarm
    gone = routine.status_for(rows, [], at(20, 0), lambda e, d: False)
    assert by_label(gone, "Evening scan")["status"] == "past"


def test_file_evidence_task_row_and_market_rows():
    rows = routine.rows_for(TUE)
    out = routine.status_for(rows, [], at(17, 20), lambda e, d: e.startswith("jsonl:"))
    # the Lab rows moved to 18:35 / 19:05 on 2026-09-08 (Tiingo posts the bar after ~17:00); a task row with
    # jsonl evidence dated today reads done whatever the clock says
    assert by_label(out, "Tiingo EOD backfill")["status"] == "done"
    # a paused row reads off, never done or late
    paused = [routine.RoutineRow("17:15", "lab", "Lab cycle (paused)", "task", evidence="jsonl:x", grace_min=90,
                                 paused="explore loop paused")]
    assert routine.status_for(paused, [], at(17, 20), lambda e, d: True)[0]["status"] == "off"
    live = [routine.RoutineRow("17:15", "lab", "Lab cycle (live)", "task", evidence="jsonl:x", grace_min=90)]
    assert routine.status_for(live, [], at(17, 20), lambda e, d: True)[0]["status"] == "done"
    assert routine.status_for(live, [], at(19, 0), lambda e, d: False)[0]["status"] == "late"
    assert by_label(out, "Market close")["status"] == "past"
    assert by_label(out, "healthchecks")["status"] == "next"
    assert by_label(out, "Suite watchdog")["status"] == "next"


def test_untimed_rows_and_output_shape():
    fri = date(2026, 9, 11)
    rows = routine.rows_for(fri)
    out = routine.status_for(rows, [row("scorecard", 18, 6, d=fri)], at(19, 0, fri), lambda e, d: False)
    assert by_label(out, "Friday: trade-review")["status"] == "yours"
    o = by_label(out, "Friday: trade-review")
    assert set(o) == {"time", "track", "track_label", "label", "kind", "status", "evidence"}
    assert o["track"] == "swing_rsi2" and o["track_label"] == "Swing·RSI2" and o["time"] == ""
    assert by_label(out, "Market open")["track_label"] == "All"


def test_now_in_another_zone_is_converted_to_et():
    rows = routine.rows_for(TUE)
    utc_now = datetime(2026, 9, 8, 13, 40, tzinfo=ZoneInfo("UTC"))  # 09:40 ET
    out = routine.status_for(rows, [], utc_now, lambda e, d: False)
    assert by_label(out, "Market open")["status"] == "past"
    assert by_label(out, "Autopilot retry")["status"] == "next"


def test_newest_run_row_wins_regardless_of_input_order():
    rows = routine.rows_for(TUE)
    out_of_order = [row("watchdog", 19, 50, result="error"), row("watchdog", 19, 30, result="ok")]
    out = routine.status_for(rows, out_of_order, at(21, 0), lambda e, d: False)
    assert by_label(out, "Suite watchdog")["status"] == "error"     # 19:50 is newest, whatever the list order
    reversed_in = list(reversed(out_of_order))
    out2 = routine.status_for(rows, reversed_in, at(21, 0), lambda e, d: False)
    assert by_label(out2, "Suite watchdog")["status"] == "error"


def test_a_paused_row_reads_off_even_with_evidence_and_never_late():
    rows = [routine.RoutineRow("17:15", "lab", "Lab cycle", "task", evidence="jsonl:x", grace_min=90, paused="depth build")]
    assert routine.status_for(rows, [], at(17, 20), lambda e, d: True)[0]["status"] == "off"
    assert routine.status_for(rows, [], at(23, 0), lambda e, d: False)[0]["status"] == "off"
