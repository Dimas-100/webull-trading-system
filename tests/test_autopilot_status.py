"""Read-only autopilot status reader (webull_api/autopilot/status.py). No network, no placement."""
import json
from datetime import datetime, timezone

from webull_api.autopilot import status


def _write_log(tmp_path, day, entries):
    d = tmp_path / "log"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{day}.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


def test_today_decisions_classifies_and_groups(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    _write_log(tmp_path, "2026-07-09", [
        {"symbol": "AAA", "side": "BUY", "source": "entry", "allow": True, "placed": True, "result": {}},
        {"symbol": "BBB", "side": "BUY", "source": "entry", "allow": False,
         "reason": "at position cap", "layer": "positions", "placed": False},
        {"symbol": "CCC", "side": "BUY", "source": "entry", "allow": False,
         "reason": "at position cap", "layer": "positions", "placed": False},
        {"symbol": "DDD", "side": "SELL", "source": "exit:stop", "allow": True,
         "placed": False, "error": "broker 500"},
    ])
    t = status.today_decisions("2026-07-09")
    assert (t["placed"], t["skipped"], t["errored"], t["decisions"]) == (1, 2, 1, 4)
    assert t["skips"] == [{"reason": "at position cap", "layer": "positions", "count": 2}]
    assert t["errors"] == [{"symbol": "DDD", "error": "broker 500"}]
    assert t["placed_orders"] == [{"symbol": "AAA", "side": "BUY", "source": "entry"}]
    assert t["last_decision_ts"] is not None


def test_today_decisions_absent_blank_and_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    assert status.today_decisions("2026-01-01")["decisions"] == 0  # no file at all
    d = tmp_path / "log"
    d.mkdir()
    (d / "2026-07-09.jsonl").write_text(
        "\n{not json}\n42\n" + json.dumps({"placed": True, "allow": True}) + "\n",
        encoding="utf-8")
    t = status.today_decisions("2026-07-09")
    assert t["placed"] == 1 and t["decisions"] == 1  # blank / corrupt / non-dict all tolerated


def test_snapshot_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.delenv("WEBULL_AUTOPILOT_ENABLED", raising=False)
    s = status.snapshot("2026-07-09")
    assert s["enabled"] is False and s["posture"] == "off" and s["armed_warning"] is False
    assert s["caps"]["max_notional"] == 40.0  # code SSOT default


def test_snapshot_armed_raises_the_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    s = status.snapshot("2026-07-09")
    assert s["enabled"] is True and s["posture"] == "armed" and s["armed_warning"] is True


def test_snapshot_kill_switch_halts_and_clears_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    (tmp_path / "KILL").write_text("halt", encoding="utf-8")  # default kill-file = <dir>/KILL
    s = status.snapshot("2026-07-09")
    assert s["kill_active"] is True and s["posture"] == "halted" and s["armed_warning"] is False


def test_snapshot_loss_halt_posture(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "2026-07-09.json").write_text(
        json.dumps({"day": "2026-07-09", "orders_today": 2, "halt_tripped": True}),
        encoding="utf-8")
    s = status.snapshot("2026-07-09")
    assert s["halt_tripped"] is True and s["posture"] == "loss_halt" and s["orders_today"] == 2


def test_snapshot_surfaces_realized_loss(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "2026-07-09.json").write_text(
        json.dumps({"day": "2026-07-09", "realized_loss": -55.0}), encoding="utf-8")
    assert status.snapshot("2026-07-09")["realized_loss"] == -55.0


def test_snapshot_go_live_approved_clears_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    (tmp_path / "GO_LIVE_APPROVED").write_text("ok", encoding="utf-8")
    s = status.snapshot("2026-07-09")
    assert s["posture"] == "armed" and s["armed_warning"] is False  # owner cleared it


def test_default_day_is_the_eastern_date_not_utc():
    """Cockpit strip, activity, decisions, Manager note and overview report all take the snapshot's
    default day; after 20:00 ET UTC has rolled to tomorrow and they read an empty day (no run, zero
    orders). The runner keys its state/audit files by the LOCAL (ET) date, so the default must too."""
    late = datetime(2026, 9, 10, 0, 30, tzinfo=timezone.utc)        # 2026-09-09 20:30 ET
    assert status.default_day(late) == "2026-09-09"
    assert status.default_day(datetime(2026, 9, 9, 13, 36, tzinfo=timezone.utc)) == "2026-09-09"
    assert status.default_day(datetime(2026, 9, 9, 3, 59, tzinfo=timezone.utc)) == "2026-09-08"


def test_snapshot_without_a_day_reads_the_default_days_state(tmp_path, monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    sd = tmp_path / "state"
    sd.mkdir()
    (sd / "2026-07-09.json").write_text(json.dumps({"day": "2026-07-09", "orders_today": 3}), encoding="utf-8")
    monkeypatch.setattr(status, "default_day", lambda now=None: "2026-07-09")
    assert status.snapshot()["orders_today"] == 3
