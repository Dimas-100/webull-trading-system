"""The paper-suite orchestrator core (runner_cli.run_suite): sequences the five paper runners, never
lets one failing runner stop the rest, aggregates their exit codes (1 hard error > 2 soft > 0),
refuses to run outside the EOD window (a Task Scheduler catch-up firing at boot pre-market must not
stamp today's run-log and block the real evening run), and gates on the network coming up first
(the task wakes the PC and fires before Wi-Fi/DNS reconnects - 2026-08-26/27 outages)."""
from datetime import datetime

from webull_web import runner_cli


def _ok(force):
    return {"result": "ok", "errors": [], "summary": "clean"}


def _gate_up():
    """A gate_fn stub: network already up (0.0s waited), no real socket touched."""
    return 0.0


def _at(hour, minute=0):
    """A now_fn pinned to a fixed ET wall-clock time."""
    return lambda: datetime(2026, 7, 15, hour, minute)


_EVENING = _at(17, 30)


def test_all_clean_exits_zero():
    code = runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up,
                                run_fns={"a": _ok}, now_fn=_EVENING)
    assert code == 0


def test_soft_errors_exit_two():
    runs = {"a": _ok, "b": lambda force: {"result": "ok", "errors": ["one symbol failed"], "summary": "b"}}
    code = runner_cli.run_suite(argv=[], runners=[("A", "a"), ("B", "b")], env_fn=lambda: None, gate_fn=_gate_up,
                                run_fns=runs, now_fn=_EVENING)
    assert code == 2


def test_hard_error_exits_one():
    runs = {"a": lambda f: {"result": "error", "errors": ["outage"], "summary": "a"}}
    code = runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up, run_fns=runs,
                                now_fn=_EVENING)
    assert code == 1


def test_a_failing_runner_does_not_stop_the_others():
    ran = []

    def boom(force):
        raise RuntimeError("down")

    def good(force):
        ran.append("ran")
        return {"result": "ok", "errors": [], "summary": "good"}

    code = runner_cli.run_suite(argv=[], runners=[("Bad", "bad"), ("Good", "good")],
                                env_fn=lambda: None, gate_fn=_gate_up, run_fns={"bad": boom, "good": good},
                                now_fn=_EVENING)
    assert code == 1 and ran == ["ran"]  # the good runner still ran despite the bad one failing


def test_force_is_forwarded_to_each_runner():
    seen = {}
    runs = {"a": lambda force: seen.update(force=force) or {"result": "ok", "errors": [], "summary": "a"}}
    runner_cli.run_suite(argv=["--force"], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up, run_fns=runs,
                         now_fn=_EVENING)
    assert seen["force"] is True


def test_outside_eod_window_skips_every_runner(capsys):
    ran = []
    runs = {"a": lambda force: ran.append("a") or {"result": "ok", "errors": [], "summary": "a"}}
    code = runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up,
                                run_fns=runs, now_fn=_at(5, 54))
    assert code == 0
    assert ran == []                                   # no runner invoked -> no run-log stamped
    assert "EOD window" in capsys.readouterr().out


def test_force_overrides_the_eod_window_gate():
    ran = []
    runs = {"a": lambda force: ran.append("a") or {"result": "ok", "errors": [], "summary": "a"}}
    code = runner_cli.run_suite(argv=["--force"], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up,
                                run_fns=runs, now_fn=_at(5, 54))
    assert code == 0 and ran == ["a"]


def test_four_pm_et_is_inside_the_window():
    ran = []
    runs = {"a": lambda force: ran.append("a") or {"result": "ok", "errors": [], "summary": "a"}}
    code = runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None, gate_fn=_gate_up,
                                run_fns=runs, now_fn=_at(16, 0))
    assert code == 0 and ran == ["a"]


def test_network_gate_runs_before_the_first_runner():
    order = []
    runs = {"a": lambda force: order.append("runner") or {"result": "ok", "errors": [], "summary": "a"}}
    runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None,
                         gate_fn=lambda: order.append("gate") or 0.0,
                         run_fns=runs, now_fn=_EVENING)
    assert order == ["gate", "runner"]


def test_gate_timeout_still_runs_runners_and_warns(capsys):
    """A REAL outage (gate returns None) must not skip the evening: the runners still run and
    record honest error rows — the gate only fixes the transient wake race, never masks."""
    ran = []
    runs = {"a": lambda force: ran.append("a") or {"result": "ok", "errors": [], "summary": "a"}}
    code = runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None,
                                gate_fn=lambda: None, run_fns=runs, now_fn=_EVENING)
    assert code == 0 and ran == ["a"]
    assert "running anyway" in capsys.readouterr().out


def test_gate_wait_is_reported(capsys):
    runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None,
                         gate_fn=lambda: 8.0, run_fns={"a": _ok}, now_fn=_EVENING)
    assert "network came up after 8s" in capsys.readouterr().out


def test_premarket_noop_never_calls_the_gate():
    """The boot-time catch-up firing pre-market must exit immediately — not sit in a
    two-minute network wait."""
    called = []
    runner_cli.run_suite(argv=[], runners=[("A", "a")], env_fn=lambda: None,
                         gate_fn=lambda: called.append(1) or 0.0,
                         run_fns={"a": _ok}, now_fn=_at(5, 54))
    assert called == []
