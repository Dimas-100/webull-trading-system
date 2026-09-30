"""Unit tests for webull_api.paths.data_dir precedence."""
from pathlib import Path

from webull_api.paths import REPO_ROOT, data_dir


def _clear(monkeypatch):
    monkeypatch.delenv("WEBULL_DATA_DIR", raising=False)
    monkeypatch.delenv("JOURNAL_DIR", raising=False)


def test_default_is_repo_anchored(monkeypatch):
    _clear(monkeypatch)
    assert data_dir("journal", "JOURNAL_DIR") == REPO_ROOT / "data" / "journal"


def test_default_without_env_var_name(monkeypatch):
    _clear(monkeypatch)
    assert data_dir("journal") == REPO_ROOT / "data" / "journal"


def test_webull_data_dir_overrides_default(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("WEBULL_DATA_DIR", str(tmp_path))
    assert data_dir("journal", "JOURNAL_DIR") == tmp_path / "journal"


def test_per_store_env_wins_over_webull_data_dir(monkeypatch, tmp_path):
    _clear(monkeypatch)
    monkeypatch.setenv("WEBULL_DATA_DIR", str(tmp_path / "tree"))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "solo"))
    assert data_dir("journal", "JOURNAL_DIR") == tmp_path / "solo"


def test_relative_env_value_anchored_under_repo(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("JOURNAL_DIR", "somewhere/rel")
    assert data_dir("journal", "JOURNAL_DIR") == REPO_ROOT / "somewhere" / "rel"


def test_relative_webull_data_dir_anchored_under_repo(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("WEBULL_DATA_DIR", "mydata")
    assert data_dir("journal", "JOURNAL_DIR") == REPO_ROOT / "mydata" / "journal"


def test_repo_root_is_the_repo(monkeypatch):
    assert (REPO_ROOT / "CLAUDE.md").is_file()
