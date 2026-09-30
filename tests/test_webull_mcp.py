import asyncio
import json
import pathlib

import webull_mcp
from webull_mcp import server
from webull_api import market_data, options_chain


def test_mcp_never_places_orders():
    """Source-level safety: the option-market-data MCP must be read-only — never import or call
    the order-placing surface (mirrors the copilot/paper safety tests)."""
    src = ""
    for p in pathlib.Path(webull_mcp.__file__).resolve().parent.rglob("*.py"):
        src += p.read_text(encoding="utf-8")
    assert "place_option" not in src
    assert "trading.place" not in src
    assert "import trading" not in src
    assert "from webull_api import" in src  # it does use the read modules


def test_option_chain_tool(monkeypatch):
    monkeypatch.setattr(market_data, "get_snapshot", lambda s: [{"price": "296"}])
    monkeypatch.setattr(
        options_chain, "fetch_chain",
        lambda *a, **k: {"symbol": "AAPL", "expiration": "2026-07-17", "spot": 296, "rows": []},
    )
    out = json.loads(server.option_chain("AAPL", "2026-07-17"))
    assert out["symbol"] == "AAPL" and out["rows"] == []


def test_option_expirations_tool(monkeypatch):
    monkeypatch.setattr(market_data, "get_snapshot", lambda s: [{"price": "296"}])
    monkeypatch.setattr(options_chain, "discover_expirations", lambda *a, **k: ["2026-07-17"])
    out = json.loads(server.option_expirations("AAPL"))
    assert out["expirations"] == ["2026-07-17"] and out["spot"] == 296.0


def test_entitlement_returns_error_not_raise(monkeypatch):
    def boom(s):
        raise market_data.MarketDataNotEntitledError("x")

    monkeypatch.setattr(market_data, "get_snapshot", boom)
    out = json.loads(server.option_chain("AAPL", "2026-07-17"))
    assert out["error"] == "MarketDataNotEntitled"


def test_tools_are_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"get_option_expirations", "get_option_chain", "get_option_snapshot",
            "get_option_analytics", "analyze_option_strategy", "get_vol_surface",
            "get_income_yields", "get_earnings_move"} <= names


def test_token_dir_is_absolute_after_load():
    import os
    from webull_mcp import env
    env.load_repo_env()
    td = os.environ.get("WEBULL_OPENAPI_TOKEN_DIR")
    # the MCP launches from C:\Windows\System32, so a relative token dir must be resolved absolute
    assert td and os.path.isabs(td)


def test_sdk_stream_logging_routed_to_stderr():
    """The SDK stream logger must never log to stdout (it's the MCP JSON-RPC channel) — the MCP
    routes it to stderr. (The SDK FILE logger is rerouted under <repo>/logs/ by webull_api.client;
    see tests/test_sdk_log_routing.py.)"""
    import logging
    import sys
    from webull_mcp import env
    from webull.core.client import ApiClient

    env.load_repo_env()  # applies the stream-logger hardening
    c = ApiClient("k", "s", "us")

    logging.getLogger("webull.core").handlers.clear()
    c.set_stream_logger(stream=sys.stdout)  # SDK passes stdout; must be redirected to stderr
    handlers = logging.getLogger("webull.core").handlers
    assert handlers and all(getattr(h, "stream", None) is not sys.stdout for h in handlers)
