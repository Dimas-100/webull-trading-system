# tests/test_autopilot_config.py
from pathlib import Path

from webull_api.autopilot.config import AutopilotConfig


def test_defaults_are_off_and_strict():
    cfg = AutopilotConfig()
    assert cfg.enabled is False
    assert cfg.max_notional == 40.0
    assert cfg.max_positions == 1
    assert cfg.max_orders_per_day == 5
    assert cfg.daily_loss_halt == 40.0
    assert cfg.max_positions_risk_off == 0
    # kill_file now defaults to an ABSOLUTE repo-anchored path (CWD-independent), not "autopilot/KILL"
    assert Path(cfg.kill_file).is_absolute()
    assert cfg.kill_file.replace("\\", "/").endswith("autopilot/KILL")


def test_from_env_missing_caps_fall_back_to_strict_defaults(monkeypatch):
    for k in ("WEBULL_AUTOPILOT_ENABLED", "WEBULL_AUTOPILOT_MAX_NOTIONAL",
              "WEBULL_AUTOPILOT_MAX_POSITIONS", "WEBULL_AUTOPILOT_DAILY_LOSS_HALT"):
        monkeypatch.delenv(k, raising=False)
    cfg = AutopilotConfig.from_env()
    assert cfg.enabled is False
    assert cfg.max_notional == 40.0
    assert cfg.max_positions == 1


def test_from_env_reads_values(monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_ENABLED", "true")
    monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_NOTIONAL", "60")
    monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_POSITIONS", "2")
    cfg = AutopilotConfig.from_env()
    assert cfg.enabled is True
    assert cfg.max_notional == 60.0
    assert cfg.max_positions == 2


def test_from_env_garbage_cap_falls_back_not_unlimited(monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_NOTIONAL", "not-a-number")
    cfg = AutopilotConfig.from_env()
    assert cfg.max_notional == 40.0


def test_from_env_non_finite_cap_falls_back_to_default(monkeypatch):
    for val in ("inf", "-inf", "nan", "Infinity"):
        monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_NOTIONAL", val)
        assert AutopilotConfig.from_env().max_notional == 40.0
    monkeypatch.setenv("WEBULL_AUTOPILOT_DAILY_LOSS_HALT", "inf")
    assert AutopilotConfig.from_env().daily_loss_halt == 40.0
    monkeypatch.setenv("WEBULL_AUTOPILOT_MAX_POSITIONS", "inf")
    assert AutopilotConfig.from_env().max_positions == 1
