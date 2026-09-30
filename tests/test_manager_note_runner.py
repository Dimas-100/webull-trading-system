"""The 9th suite step: compose -> store -> push (best-effort) -> run-log."""
import inspect
import json

from webull_web import manager_note_service as svc


def _quiet_sources(monkeypatch):
    monkeypatch.setattr(svc, "_gather", lambda today: dict(
        run_rows=[], netliq_rows=[], autopilot=None, real_stops=None))


def test_composes_stores_and_reports_push_off(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.delenv("WEBULL_MANAGER_NOTE_NTFY", raising=False)
    _quiet_sources(monkeypatch)
    rep = svc.run()
    assert rep["result"] == "ok" and rep["pushed"] == "off"
    rows = [json.loads(l) for l in (tmp_path / "manager_notes.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["text"]
    runs = [json.loads(l) for l in (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    assert runs[-1]["key"] == "note" and runs[-1]["result"] == "ok"


def test_push_called_when_env_set_and_failure_keeps_run_ok(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_MANAGER_NOTE_NTFY", "https://ntfy.sh/topic")
    _quiet_sources(monkeypatch)
    calls = {}
    monkeypatch.setattr(svc.push, "push_text",
                        lambda text, *, title, url, **kw: calls.update(url=url, **kw) or False)
    rep = svc.run()
    assert calls["url"] == "https://ntfy.sh/topic"
    # Quiet sources -> every "unavailable" item lands in ATTENTION -> the push is high/warning.
    assert calls["priority"] == "high" and calls["tags"] == "warning"
    assert rep["result"] == "ok" and rep["pushed"] is False   # failed push never fails the run
    assert "attention" in rep["summary"]


def test_same_day_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.delenv("WEBULL_MANAGER_NOTE_NTFY", raising=False)
    _quiet_sources(monkeypatch)
    assert svc.run()["result"] == "ok"
    assert svc.run()["result"] == "no_op"
    assert svc.run(force=True)["result"] == "ok"


def test_runs_as_its_own_step_after_the_suite():
    # 2026-09-29: the note left the 17:30 suite so it runs after the 18:15 autopilot backstop
    from webull_web import runner_cli
    assert ("Note", "manager_note_service") not in runner_cli.PAPER_SUITE
    assert runner_cli.NOTE_STEP == [("Note", "manager_note_service")]


def test_note_surface_has_no_submit_path():
    src = inspect.getsource(svc)
    for forbidden in ("trading.place", "import trading", "place_order", "confirm=True"):
        assert forbidden not in src, f"{svc.__name__} must not contain {forbidden!r}"
