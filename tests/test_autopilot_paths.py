# tests/test_autopilot_paths.py
"""The autopilot store + kill-file must resolve to a REPO-anchored absolute path regardless of CWD,
so the kill-switch works identically from Desktop, a script, or the Windows scheduler."""
import os
from pathlib import Path

from webull_api.autopilot import paths
from webull_api.autopilot.config import AutopilotConfig

_REPO = Path(paths.__file__).resolve().parents[2]


def test_dir_defaults_under_repo(monkeypatch):
    monkeypatch.delenv("WEBULL_AUTOPILOT_DIR", raising=False)
    assert paths.autopilot_dir() == _REPO / "autopilot"


def test_relative_dir_is_anchored_under_repo(monkeypatch):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", "autopilot")
    assert paths.autopilot_dir() == _REPO / "autopilot"


def test_absolute_dir_is_respected(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    assert paths.autopilot_dir() == tmp_path


def test_kill_file_default_is_absolute_under_repo(monkeypatch):
    monkeypatch.delenv("WEBULL_AUTOPILOT_KILL_FILE", raising=False)
    cfg = AutopilotConfig.from_env()
    assert Path(cfg.kill_file).is_absolute()
    assert Path(cfg.kill_file) == _REPO / "autopilot" / "KILL"


def test_relative_kill_file_is_anchored_under_repo(monkeypatch):
    # The exact fragile case the user had in .env: WEBULL_AUTOPILOT_KILL_FILE=autopilot/KILL
    monkeypatch.setenv("WEBULL_AUTOPILOT_KILL_FILE", "autopilot/KILL")
    cfg = AutopilotConfig.from_env()
    assert Path(cfg.kill_file) == _REPO / "autopilot" / "KILL"


def test_absolute_kill_file_is_respected(monkeypatch):
    target = "C:/Users/someone/OneDrive/webull/KILL" if os.name == "nt" else "/mnt/onedrive/KILL"
    monkeypatch.setenv("WEBULL_AUTOPILOT_KILL_FILE", target)
    cfg = AutopilotConfig.from_env()
    assert Path(cfg.kill_file) == Path(target)


def test_default_kill_file_follows_the_store_dir(monkeypatch, tmp_path):
    # Moving the store root moves the DEFAULT kill-file with it (state/audit/kill share a root).
    monkeypatch.delenv("WEBULL_AUTOPILOT_KILL_FILE", raising=False)
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path))
    cfg = AutopilotConfig.from_env()
    assert Path(cfg.kill_file) == tmp_path / "KILL"


def test_explicit_kill_file_overrides_the_store_dir(monkeypatch, tmp_path):
    # An explicit kill-file (e.g. a phone-synced path) wins over WEBULL_AUTOPILOT_DIR.
    override = tmp_path / "synced" / "KILL"
    monkeypatch.setenv("WEBULL_AUTOPILOT_DIR", str(tmp_path / "store"))
    monkeypatch.setenv("WEBULL_AUTOPILOT_KILL_FILE", str(override))
    cfg = AutopilotConfig.from_env()
    assert Path(cfg.kill_file) == override
