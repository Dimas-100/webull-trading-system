"""Shared pytest fixtures.

Every fixture here is autouse and keeps a test hermetic: off the live account, the network and the repo's
real data stores. (The web app's order-route price-fetch fixture went with the web app, 2026-09-28.)
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_position_plans_store(monkeypatch, tmp_path_factory):
    """position_plans.save_plan (written by screen_draft/discover_and_draft) resolves POSITION_PLANS_DIR
    to a repo-relative default; without isolation, tests that drive screen_draft write fabricated plan
    records into the REAL position_plans/ dir that reconcile.scan_unprotected reads to price protective
    stops. Point every test at a throwaway dir by default; tests that assert on the store override it
    with their own monkeypatch.setenv."""
    monkeypatch.setenv("POSITION_PLANS_DIR", str(tmp_path_factory.mktemp("position_plans")))


@pytest.fixture(autouse=True)
def _no_journal_sync_in_mcp_place(monkeypatch):
    """The paper/trade MCP place + account tools now reconcile the Journal as a best-effort side-effect.
    Neutralize that by default so existing unit tests stay hermetic (no disk/network); the dedicated
    journaling tests override these stubs or test the ingest layer directly."""
    for mod in ("webull_trade_mcp.server",):
        for name in ("_journal_paper_fills", "_journal_real_fills", "_journal_options_fills"):
            try:
                import importlib
                monkeypatch.setattr(importlib.import_module(mod), name, lambda *a, **k: 0, raising=False)
            except Exception:
                pass


@pytest.fixture(autouse=True)
def _isolate_exec_ledger(monkeypatch, tmp_path_factory):
    """trading.place now appends a post-submit decision record (exec-quality ledger) with a
    best-effort live mark fetch. Point the store at a throwaway dir and stub the mark so every
    submit-path test stays hermetic (no repo-dir pollution, no network); the dedicated ledger
    tests override with their own monkeypatch."""
    monkeypatch.setenv("WEBULL_EXEC_DIR", str(tmp_path_factory.mktemp("exec_ledger")))
    try:
        from webull_api import exec_ledger
        monkeypatch.setattr(exec_ledger, "_mark", lambda symbol: None)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _lab_env_defaults(monkeypatch):
    """The operator's .env carries the Lab's bar source and lookback (WEBULL_LAB_BAR_SOURCE=tiingo,
    WEBULL_LAB_LOOKBACK_BARS=1900 since the 2026-09-08 re-enable) and the package loads .env on
    import. Tests must see the code defaults (webull, 750) unless they set the variables themselves,
    otherwise the Lab suites route to an empty tmp Tiingo store and refuse every cycle."""
    monkeypatch.delenv("WEBULL_LAB_BAR_SOURCE", raising=False)
    monkeypatch.delenv("WEBULL_LAB_LOOKBACK_BARS", raising=False)


@pytest.fixture(autouse=True)
def _isolate_tiingo_store(request, monkeypatch, tmp_path_factory):
    """The real data/tiingo store now feeds the swing screen (setups_service) and the Lab; a unit
    test that reaches store.read/last_date for a real symbol (SPY, AAPL...) would silently read live
    data. Point TIINGO_DIR at a throwaway directory for every test except the `store`-marked ones,
    which exist to run against the real store. A test that sets TIINGO_DIR itself still wins."""
    if request.node.get_closest_marker("store") is None:
        monkeypatch.setenv("TIINGO_DIR", str(tmp_path_factory.mktemp("tiingo")))
