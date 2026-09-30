"""Guard: every store resolver routes through webull_api.paths.data_dir.

With WEBULL_DATA_DIR set and every per-store override cleared, each resolver must land
under <WEBULL_DATA_DIR>/<store>. A module regressing to a bare CWD-relative default
("activity", "journal", ...) fails here. Tasks extend RESOLVERS as stores migrate."""
import importlib
from pathlib import Path

import pytest

PER_STORE_ENVS = [
    "ACTIVITY_DIR", "JOURNAL_DIR", "PAPER_DIR", "LAB_DIR", "ORDER_INTENTS_DIR",
    "POSITION_PLANS_DIR", "PRACTICE_DIR", "STRATEGY_DIR", "RSI2_STATE_DIR", "WEBULL_EXEC_DIR",
    "WEBULL_SCANNER_DIR",
]

RESOLVERS = [
    ("webull_web.practice_store", "_dir", "practice"),
    ("webull_web.position_plans", "_dir", "plans"),
    ("webull_web.lab_store", "lab_dir", "lab"),
    ("webull_web.intent_store", "_dir", "intents"),
    ("webull_web.paper_store", "_path", "paper"),
    ("webull_web.paper_options_store", "_path", "paper"),
    ("webull_web.proven_store", "_path", "paper"),
    ("webull_api.action_log", "_dir", "activity"),
    ("webull_api.run_log", "_dir", "activity"),
    ("webull_api.exec_ledger", "_dir", "exec"),
    ("webull_web.contributions_store", "_dir", "activity"),
    ("webull_web.manager_notes_store", "_dir", "activity"),
    ("webull_web.netliq_store", "_dir", "activity"),
    ("webull_web.runner_util", "_activity_dir", "activity"),
    ("webull_web.rsi2_store", "_dir", "activity"),
    ("webull_web.journal_store", "_dir", "journal"),
    ("webull_web.scanner_store", "_dir", "scanner"),
]


def _isolate(monkeypatch, tmp_path):
    for var in PER_STORE_ENVS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WEBULL_DATA_DIR", str(tmp_path))


@pytest.mark.parametrize("mod_name,fn_name,store", RESOLVERS)
def test_resolver_routes_through_data_dir(monkeypatch, tmp_path, mod_name, fn_name, store):
    _isolate(monkeypatch, tmp_path)
    mod = importlib.import_module(mod_name)
    out = Path(getattr(mod, fn_name)())
    if out.suffix:  # _path()-style resolvers return a file inside the store dir
        out = out.parent
    assert out == tmp_path / store


def test_rsi2_follows_activity_dir_when_own_var_unset(monkeypatch, tmp_path):
    """The two-var split is dead: without RSI2_STATE_DIR, rsi2 state lives with ACTIVITY_DIR."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "act"))
    from webull_web import rsi2_store
    assert Path(rsi2_store._dir()) == tmp_path / "act"


def test_rsi2_own_var_still_wins(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "act"))
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path / "rsi2"))
    from webull_web import rsi2_store
    assert Path(rsi2_store._dir()) == tmp_path / "rsi2"


_OLD_DEFAULT = __import__("re").compile(
    r'os\.environ\.get\(\s*"(?:ACTIVITY_DIR|JOURNAL_DIR|PAPER_DIR|LAB_DIR|ORDER_INTENTS_DIR'
    r'|POSITION_PLANS_DIR|PRACTICE_DIR|STRATEGY_DIR|RSI2_STATE_DIR)"\s*,\s*"'
)


def test_no_bare_relative_store_defaults_remain():
    """Tripwire: the legacy os.environ.get("X_DIR", "<relative>") pattern must not reappear."""
    repo = Path(__file__).resolve().parents[1]
    offenders = []
    for pkg in ("webull_api", "webull_web", "scripts", "webull_mcp", "webull_trade_mcp"):
        for f in (repo / pkg).rglob("*.py"):
            if _OLD_DEFAULT.search(f.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(f.relative_to(repo)))
    assert offenders == []
