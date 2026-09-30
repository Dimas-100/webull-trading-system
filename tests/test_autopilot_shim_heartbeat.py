"""The autopilot SHIM (scripts/autopilot.py cmd_run) appends the run-log heartbeat the
watchdog's autopilot coverage detects. Observability only: webull_api/autopilot/* and the
gate surfaces are byte-untouched (test_gate_hardening pins that separately)."""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_spec = importlib.util.spec_from_file_location("autopilot_shim", REPO / "scripts" / "autopilot.py")
shim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shim)


class _Cfg:
    enabled = True


def _report(errors=()):
    return {"placed": [{"symbol": "AAPL", "side": "SELL", "source": "protect"}],
            "skipped": [], "errors": list(errors),
            "message": "Autopilot: placed 1 order(s), skipped 0, 0 error(s)."}


def _patch_run(monkeypatch, report, gate=lambda: 0.0):
    import webull_api.net_gate as net_gate
    monkeypatch.setattr(net_gate, "wait_for_network", gate)
    import webull_api.autopilot.run as run_mod
    monkeypatch.setattr(run_mod, "run", lambda cfg=None: report)


def test_heartbeat_row_appended_ok(monkeypatch):
    import webull_api.run_log as run_log
    rows: list[dict] = []
    monkeypatch.setattr(run_log, "append", lambda r: rows.extend(r) or len(r))
    _patch_run(monkeypatch, _report())
    assert shim.cmd_run(_Cfg()) == 0
    assert rows[0]["key"] == "autopilot" and rows[0]["result"] == "ok"
    assert rows[0]["placed"] == 1 and "Autopilot" in rows[0]["summary"]


def test_heartbeat_row_error_result_on_errors(monkeypatch):
    import webull_api.run_log as run_log
    rows: list[dict] = []
    monkeypatch.setattr(run_log, "append", lambda r: rows.extend(r) or len(r))
    _patch_run(monkeypatch, _report(errors=[{"stage": "entries", "error": "boom"}]))
    assert shim.cmd_run(_Cfg()) == 0
    assert rows[0]["result"] == "error" and rows[0]["errors"]


def test_heartbeat_written_even_when_disabled(monkeypatch):
    import webull_api.run_log as run_log
    rows: list[dict] = []
    monkeypatch.setattr(run_log, "append", lambda r: rows.extend(r) or len(r))
    _patch_run(monkeypatch, _report())
    cfg = _Cfg()
    cfg.enabled = False
    assert shim.cmd_run(cfg) == 0
    assert rows and rows[0]["key"] == "autopilot"  # disabled is still "reported"


def test_heartbeat_failure_never_breaks_cmd_run(monkeypatch, capsys):
    import webull_api.run_log as run_log
    def boom(rows):
        raise OSError("disk gone")
    monkeypatch.setattr(run_log, "append", boom)
    _patch_run(monkeypatch, _report())
    assert shim.cmd_run(_Cfg()) == 0
    assert "Autopilot" in capsys.readouterr().out  # report still printed


def test_network_gate_runs_before_the_run(monkeypatch):
    """The autopilot's own wake timer can be what wakes the PC (the 5:45 task), so cmd_run
    must wait out the Wi-Fi/DNS reconnect race before the cycle reads decisions/broker."""
    import webull_api.run_log as run_log
    monkeypatch.setattr(run_log, "append", lambda r: len(r))
    order = []
    import webull_api.autopilot.run as run_mod
    _patch_run(monkeypatch, _report(), gate=lambda: order.append("gate") or 0.0)
    monkeypatch.setattr(run_mod, "run", lambda cfg=None: order.append("run") or _report())
    assert shim.cmd_run(_Cfg()) == 0
    assert order == ["gate", "run"]


def test_gate_timeout_still_runs_and_warns(monkeypatch, capsys):
    """Gate timeout must not skip the run: autopilot already fails closed with no data, and
    skipping would also drop the heartbeat row the watchdog counts on."""
    import webull_api.run_log as run_log
    rows: list[dict] = []
    monkeypatch.setattr(run_log, "append", lambda r: rows.extend(r) or len(r))
    _patch_run(monkeypatch, _report(), gate=lambda: None)
    assert shim.cmd_run(_Cfg()) == 0
    assert rows and rows[0]["key"] == "autopilot"  # heartbeat still written
    assert "network still unreachable" in capsys.readouterr().out
