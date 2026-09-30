"""Dead-man's watchdog: assess is pure; run() no-ops outside the weekday evening window
(a boot-time catch-up firing must not false-alarm) and alerts LOUD on dead/degraded."""
import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from webull_web import manager_note_service
from webull_web import watchdog_service as svc

TODAY = "2026-07-24"  # a Friday


def _row(key, ts=f"{TODAY}T17:35:00", result="ok"):
    return {"key": key, "ts": ts, "result": result}


def _all_rows():
    return [_row(k) for k in svc.EXPECTED_KEYS]


def test_expected_keys_track_the_suite():
    labels = {k for k, _ in manager_note_service.RUNNER_LABELS}
    # autopilot is DELIBERATELY extra: the 5:45 PM real-money task reports into the same
    # run-log but is not a suite runner (no manager-note label).
    # paper_day is DELIBERATELY absent since 2026-09-14: the paper day session is PAUSED (owner
    # cancelled it; task disabled), so a missing row must not read "degraded" every night.
    assert set(svc.EXPECTED_KEYS) == labels | {"netliq", "note", "autopilot"}
    # SIMPLIFIED 2026-09-29: the watchdog expects exactly the live suite + autopilot, no parked key
    from webull_web import runner_cli
    assert set(svc.EXPECTED_KEYS) == {"netliq", "rsi2_real", "flows", "note", "autopilot"}
    assert len(runner_cli.PAPER_SUITE) + len(runner_cli.NOTE_STEP) == len(svc.EXPECTED_KEYS) - 1
    parked = {k for k, _ in manager_note_service.PARKED_RUNNER_LABELS}
    assert not parked & set(svc.EXPECTED_KEYS)


def test_ok_when_all_reported():
    v = svc.assess(_all_rows(), TODAY)
    assert v["state"] == "ok" and v["missing"] == []


def test_dead_when_note_missing():
    v = svc.assess([r for r in _all_rows() if r["key"] != "note"], TODAY)
    assert v["state"] == "dead" and "note" in v["missing"]


def test_degraded_when_one_runner_never_reported():
    v = svc.assess([r for r in _all_rows() if r["key"] != "flows"], TODAY)
    assert v["state"] == "degraded" and v["missing"] == ["flows"]


def test_paused_paper_day_session_is_not_expected():
    # PAUSED 2026-09-14 (owner): the 09:25 task is disabled, so its absence is healthy, not degraded.
    assert "paper_day" not in svc.EXPECTED_KEYS
    v = svc.assess(_all_rows(), TODAY)
    assert v["state"] == "ok" and "paper_day" not in v.get("missing", [])


def test_error_result_still_counts_as_reported():
    rows = [_row(k, result="error") for k in svc.EXPECTED_KEYS]
    assert svc.assess(rows, TODAY)["state"] == "ok"


def test_yesterdays_rows_do_not_count():
    rows = [_row(k, ts="2026-07-23T17:35:00") for k in svc.EXPECTED_KEYS]
    assert svc.assess(rows, TODAY)["state"] == "dead"


def test_autopilot_silent_when_only_autopilot_missing():
    v = svc.assess([r for r in _all_rows() if r["key"] != "autopilot"], TODAY)
    assert v["state"] == "autopilot-silent" and v["missing"] == ["autopilot"]
    assert "real-money" in v["detail"].lower()


def test_autopilot_silent_outranks_degraded_but_not_dead():
    v = svc.assess([r for r in _all_rows() if r["key"] not in ("autopilot", "flows")], TODAY)
    assert v["state"] == "autopilot-silent" and set(v["missing"]) == {"autopilot", "flows"}
    v2 = svc.assess([r for r in _all_rows() if r["key"] not in ("autopilot", "note")], TODAY)
    assert v2["state"] == "dead"


# ---- run() ----

def _at(h, m, day=24):  # 2026-07-24 Fri; 25 Sat
    return lambda: datetime(2026, 7, day, h, m, tzinfo=ZoneInfo("America/New_York"))


def _capture_runlog(monkeypatch, rows):
    appended: list[dict] = []
    monkeypatch.setattr("webull_api.run_log.load", lambda: rows)
    monkeypatch.setattr("webull_api.run_log.append", lambda r: appended.extend(r) or len(r))
    return appended


def _note_row(day):
    return {"key": "note", "ts": f"{day}T17:35:00", "result": "ok"}


def test_lookback_target_skips_weekends():
    assert svc.lookback_target(_at(7, 5, day=27)()) == "2026-07-24"   # Mon -> Fri
    assert svc.lookback_target(_at(7, 5, day=23)()) == "2026-07-22"   # Thu -> Wed
    assert svc.lookback_target(_at(10, 0, day=25)()) == "2026-07-24"  # Sat -> Fri
    assert svc.lookback_target(_at(10, 0, day=26)()) == "2026-07-24"  # Sun -> Fri


def test_lookback_covered_by_note_watchdog_or_prior_lookback():
    t = "2026-07-24"
    assert svc.lookback_covered([_note_row(t)], t)
    assert svc.lookback_covered([{"key": "watchdog", "ts": f"{t}T19:00:00"}], t)
    assert svc.lookback_covered(
        [{"key": "watchdog", "ts": "2026-07-27T08:00:00", "looked_back": t}], t)
    assert not svc.lookback_covered([_note_row("2026-07-23")], t)


def test_lookback_covered_note_present_but_autopilot_missing_that_day_not_covered():
    # (i) target-day note row present + an autopilot row on some OTHER day -> NOT covered:
    # a note alone no longer proves the 5:45 PM real-money run reported that evening.
    t = "2026-07-24"
    rows = [_note_row(t), {"key": "autopilot", "ts": "2026-07-20T17:45:00", "result": "ok"}]
    assert not svc.lookback_covered(rows, t)


def test_run_note_without_autopilot_backalerts_naming_autopilot(monkeypatch):
    # (i) at the run() level: the back-alert push names autopilot and the row is appended
    # with looked_back == target.
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    target = "2026-07-23"  # Thu; Fri 07:05 boot's look-back target
    rows = [_note_row(target), {"key": "autopilot", "ts": "2026-07-20T17:45:00", "result": "ok"}]
    appended = _capture_runlog(monkeypatch, rows)
    pushes: list[str] = []
    code = svc.run(now_fn=_at(7, 5),
                   push_fn=lambda text, *, title, url, **kw: pushes.append(text) or True)
    assert code == 1
    assert "autopilot" in pushes[0].lower()
    assert appended[0]["looked_back"] == target


def test_lookback_covered_note_with_same_day_autopilot():
    # (ii) target-day note row + target-day autopilot row -> covered.
    t = "2026-07-24"
    rows = [_note_row(t), {"key": "autopilot", "ts": f"{t}T17:46:00", "result": "ok"}]
    assert svc.lookback_covered(rows, t)


def test_lookback_covered_note_only_no_autopilot_anywhere_is_transition_guard():
    # (iii) target-day note row, no autopilot row anywhere -> covered (transition guard:
    # pre-heartbeat history must not false-alarm).
    t = "2026-07-24"
    assert svc.lookback_covered([_note_row(t)], t)


def test_lookback_covered_same_day_watchdog_before_1830_not_covered():
    # (iv) a forced/boot 10 AM watchdog row (no looked_back) must not count as evening
    # coverage -- closes the "forced 10 AM watchdog row counts as evening coverage" hole.
    t = "2026-07-24"
    rows = [{"key": "watchdog", "ts": f"{t}T10:00:00"}]
    assert not svc.lookback_covered(rows, t)


def test_consecutive_outage_backalert_row_is_not_evening_coverage(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    # Wed-morning back-alert for Tue; Wed evening then ALSO went dark.
    rows = [{"key": "watchdog", "ts": "2026-07-22T07:05:00", "result": "error",
             "looked_back": "2026-07-21"}]
    appended = _capture_runlog(monkeypatch, rows)
    code = svc.run(now_fn=_at(7, 5, day=23),  # Thu boot; target = Wed 07-22
                   push_fn=lambda *a, **k: True)
    assert code == 1 and appended[0]["looked_back"] == "2026-07-22"


def test_run_before_window_covered_is_quiet(monkeypatch):
    appended = _capture_runlog(monkeypatch, [_note_row("2026-07-23")])  # Thu covered
    assert svc.run(now_fn=_at(7, 5)) == 0   # Fri morning boot catch-up
    assert appended == []                    # no row spam when covered


def test_run_before_window_uncovered_back_alerts(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [])
    pushes: list[str] = []
    code = svc.run(now_fn=_at(7, 5),
                   push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True)
    assert code == 1 and "BACK-ALERT" in pushes[0]
    assert appended[0]["looked_back"] == "2026-07-23"


def test_run_weekend_uncovered_back_alerts_friday(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [])
    code = svc.run(now_fn=_at(10, 0, day=25),  # Sat boot catch-up
                   push_fn=lambda *a, **k: True)
    assert code == 1 and appended[0]["looked_back"] == "2026-07-24"


def test_run_weekend_covered_is_quiet(monkeypatch):
    appended = _capture_runlog(monkeypatch, [_note_row("2026-07-24")])
    assert svc.run(now_fn=_at(10, 0, day=25)) == 0
    assert appended == []


def test_lookback_unreadable_runlog_is_itself_the_alert(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr("webull_api.run_log.load", boom)
    monkeypatch.setattr("webull_api.run_log.append", lambda r: len(r))
    pushes: list[str] = []
    code = svc.run(now_fn=_at(7, 5),
                   push_fn=lambda text, *, title, url, **kw: pushes.append(text) or True)
    assert code == 1 and "unreadable" in pushes[0]


def test_run_dead_pushes_and_exits_1(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [])
    pushes: list[str] = []
    code = svc.run(now_fn=_at(19, 0),
                   push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True)
    assert code == 1 and pushes and "DEAD" in pushes[0]
    assert appended[0]["key"] == "watchdog" and appended[0]["result"] == "error"


def test_run_ok_no_push_appends_ok_row(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [_row(k) for k in svc.EXPECTED_KEYS] + [_feeder_row(YESTERDAY)])
    pushes: list[str] = []
    code = svc.run(now_fn=_at(19, 0), push_fn=lambda *a, **k: pushes.append("x") or True)
    assert code == 0 and pushes == [] and appended[0]["result"] == "ok"


def test_run_push_failure_still_exits_1(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [])
    code = svc.run(now_fn=_at(19, 0), push_fn=lambda *a, **k: False)
    assert code == 1 and "push FAILED" in appended[0]["summary"]


def test_run_missing_ntfy_is_honest(monkeypatch):
    monkeypatch.delenv("WEBULL_MANAGER_NOTE_NTFY", raising=False)
    appended = _capture_runlog(monkeypatch, [])
    code = svc.run(now_fn=_at(19, 0), push_fn=lambda *a, **k: True)
    assert code == 1 and "undeliverable" in appended[0]["summary"]


def test_run_unreadable_runlog_is_itself_the_alert(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    def boom():
        raise OSError("disk gone")
    monkeypatch.setattr("webull_api.run_log.load", boom)
    monkeypatch.setattr("webull_api.run_log.append", lambda r: len(r))
    pushes: list[str] = []
    code = svc.run(now_fn=_at(19, 0),
                   push_fn=lambda text, *, title, url, **kw: pushes.append(text) or True)
    assert code == 1 and "unreadable" in pushes[0]


# ---- ping (Task 3) ----

def test_ping_fires_on_evening_run_any_verdict(monkeypatch):
    monkeypatch.setenv("WEBULL_WATCHDOG_HEALTHCHECK_URL", "https://hc.example/uuid")
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    pings: list[str] = []
    _capture_runlog(monkeypatch, [_row(k) for k in svc.EXPECTED_KEYS])
    svc.run(now_fn=_at(19, 0), ping_fn=lambda url: pings.append(url) or True)
    _capture_runlog(monkeypatch, [])  # dead evening still pings — "the watchdog ran"
    svc.run(now_fn=_at(19, 0), push_fn=lambda *a, **k: True,
            ping_fn=lambda url: pings.append(url) or True)
    assert pings == ["https://hc.example/uuid"] * 2


def test_ping_not_fired_on_lookback_or_unset(monkeypatch):
    monkeypatch.setenv("WEBULL_WATCHDOG_HEALTHCHECK_URL", "https://hc.example/uuid")
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    pings: list[str] = []
    _capture_runlog(monkeypatch, [_note_row("2026-07-23")])
    svc.run(now_fn=_at(7, 5), ping_fn=lambda url: pings.append(url) or True)  # look-back
    assert pings == []
    monkeypatch.delenv("WEBULL_WATCHDOG_HEALTHCHECK_URL")
    _capture_runlog(monkeypatch, [_row(k) for k in svc.EXPECTED_KEYS])
    svc.run(now_fn=_at(19, 0), ping_fn=lambda url: pings.append(url) or True)
    assert pings == []  # env unset -> feature off


def test_ping_failure_lands_in_summary_not_exit_code(monkeypatch):
    monkeypatch.setenv("WEBULL_WATCHDOG_HEALTHCHECK_URL", "https://hc.example/uuid")
    appended = _capture_runlog(monkeypatch, [_row(k) for k in svc.EXPECTED_KEYS])
    code = svc.run(now_fn=_at(19, 0), ping_fn=lambda url: False)
    assert code == 0 and "ping FAILED" in appended[0]["summary"]


def test_autopilot_silent_push_title_has_no_hyphen(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    _capture_runlog(monkeypatch, [r for r in [_row(k) for k in svc.EXPECTED_KEYS]
                                  if r["key"] != "autopilot"])
    pushes: list[str] = []
    svc.run(now_fn=_at(19, 0), push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True)
    assert "AUTOPILOT SILENT" in pushes[0] and "AUTOPILOT-SILENT" not in pushes[0]


def test_rsi2_real_is_an_expected_runner_key():
    from webull_web import watchdog_service
    assert "rsi2_real" in watchdog_service.EXPECTED_KEYS


def test_the_parked_session_grid_paper_session_is_not_expected():
    # SIMPLIFIED 2026-09-29 (owner: RSI2-only): the 09:25 session-grid task is disabled, so its
    # absence is healthy, not degraded (re-add the key when the task is re-enabled).
    assert "session_grid_paper" not in svc.EXPECTED_KEYS
    v = svc.assess(_all_rows(), TODAY)
    assert v["state"] == "ok" and "session_grid_paper" not in v.get("missing", [])


# ---- lab advisory (bench_feeder, previous weekday) ----

YESTERDAY = "2026-07-23"  # the Thursday before TODAY


def _feeder_row(day, result="ok"):
    return {"key": "bench_feeder", "ts": f"{day}T18:41:00-04:00", "result": result}


@pytest.fixture
def feeder_lane(monkeypatch):
    """The live lane is empty since the bench was archived (2026-09-29); these tests re-arm it to pin the mechanism."""
    monkeypatch.setattr(svc, "ADVISORY_KEYS", ("bench_feeder",))


def test_advisory_keys_are_not_suite_keys():
    assert svc.ADVISORY_KEYS == ()          # bench_feeder archived 2026-09-29
    assert not set(svc.ADVISORY_KEYS) & set(svc.EXPECTED_KEYS)


def test_advisory_missing_when_previous_weekday_has_no_feeder_row(feeder_lane):
    assert svc.advisory([_feeder_row(TODAY)], YESTERDAY) == ["bench_feeder"]


def test_advisory_clear_when_previous_weekday_reported_even_as_skip_or_error():
    assert svc.advisory([_feeder_row(YESTERDAY, "skipped")], YESTERDAY) == []
    assert svc.advisory([_feeder_row(YESTERDAY, "error")], YESTERDAY) == []


def test_run_ok_suite_with_missing_feeder_pushes_advisory_but_stays_ok(monkeypatch, feeder_lane):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [_row(k) for k in svc.EXPECTED_KEYS])
    pushes: list[tuple[str, str]] = []
    code = svc.run(now_fn=_at(19, 0),
                   push_fn=lambda text, *, title, url, **kw: pushes.append((title, text)) or True)
    assert code == 0                                   # an advisory is not an alert state
    assert len(pushes) == 1 and "ADVISORY" in pushes[0][0]
    assert "bench_feeder" in pushes[0][1] and YESTERDAY in pushes[0][1]
    assert appended[0]["result"] == "ok"
    assert "advisory bench_feeder missing 2026-07-23" in appended[0]["summary"]


def test_run_monday_advisory_looks_back_to_friday(monkeypatch):
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    monday = "2026-07-27"
    rows = [{"key": k, "ts": f"{monday}T17:35:00", "result": "ok"} for k in svc.EXPECTED_KEYS]
    rows.append(_feeder_row("2026-07-24"))               # Friday reported; the weekend has no feeder
    _capture_runlog(monkeypatch, rows)
    pushes: list[str] = []
    code = svc.run(now_fn=_at(19, 0, day=27), push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True)
    assert code == 0 and pushes == []


def test_run_dead_suite_does_not_also_push_advisory(monkeypatch, feeder_lane):
    # one push per evening: the suite alert already names the night; the advisory rides in its summary
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    appended = _capture_runlog(monkeypatch, [])
    pushes: list[str] = []
    code = svc.run(now_fn=_at(19, 0), push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True)
    assert code == 1 and len(pushes) == 1 and "DEAD" in pushes[0]
    assert "advisory bench_feeder missing" in appended[0]["summary"]


def test_lookback_path_does_not_push_advisory(monkeypatch):
    # a boot-time catch-up judges whole evenings; the advisory belongs to the evening run only
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.example/t")
    _capture_runlog(monkeypatch, [_note_row(YESTERDAY), {"key": "autopilot", "ts": f"{YESTERDAY}T17:46:00", "result": "ok"}])
    pushes: list[str] = []
    assert svc.run(now_fn=_at(7, 5), push_fn=lambda text, *, title, url, **kw: pushes.append(title) or True) == 0
    assert pushes == []
