import asyncio
import json

from webull_trade_mcp import server


def test_draft_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "draft_order" in names


def test_draft_writes_pending_intent(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.trading, "preview", lambda acct, o: {"estimated_cost": "170.00"})
    out = json.loads(server.draft("NVDA", "BUY", "1", "LIMIT", "170", thesis="trend"))
    assert out["drafted"]["symbol"] == "NVDA" and out["drafted"]["status"] == "pending"
    assert out["drafted"]["thesis"] == "trend" and out["preview"]["estimated_cost"] == "170.00"


def test_draft_never_places(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.trading, "preview", lambda acct, o: {})
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    server.draft("NVDA", "BUY", "1", "LIMIT", "170")
    assert placed["n"] == 0


def test_draft_validation_error(tmp_path, monkeypatch):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    out = json.loads(server.draft("NVDA", "HODL", "1", "LIMIT", "170"))  # bad side
    assert out["error"] == "OrderValidationError"


def test_place_order_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"draft_order", "place_order"} <= names


def _block_place(monkeypatch):
    placed = {"n": 0}
    def fake(acct, order, confirm=False):
        placed["n"] += 1
        return {"submitted": True}
    monkeypatch.setattr(server.trading, "place", fake)
    return placed


def test_place_disabled_without_codeword(monkeypatch):
    monkeypatch.delenv("WEBULL_TRADE_CODEWORD", raising=False)
    placed = _block_place(monkeypatch)
    out = json.loads(server.place("PLUG", "BUY", "1", "whatever", "LIMIT", "2.50"))
    assert out["error"] == "DirectTradingDisabled" and placed["n"] == 0


def test_place_bad_codeword(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    placed = _block_place(monkeypatch)
    out = json.loads(server.place("PLUG", "BUY", "1", "wrong", "LIMIT", "2.50"))
    assert out["error"] == "BadCodeword" and placed["n"] == 0


def test_place_over_cap(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    monkeypatch.setenv("WEBULL_TRADE_MAX_NOTIONAL", "500")
    placed = _block_place(monkeypatch)
    out = json.loads(server.place("NVDA", "BUY", "10", "secret", "LIMIT", "170"))  # 1700 > 500
    assert out["error"] == "OverCap" and placed["n"] == 0


def test_place_submits_when_all_pass(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    monkeypatch.setenv("WEBULL_TRADE_MAX_NOTIONAL", "500")
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    calls = {"n": 0, "confirm": None}
    def fake(acct, order, confirm=False):
        calls["n"] += 1
        calls["confirm"] = confirm
        return {"submitted": True}
    monkeypatch.setattr(server.trading, "place", fake)
    out = json.loads(server.place("PLUG", "BUY", "1", "secret", "LIMIT", "2.50"))  # 2.50 <= 500
    assert out["placed"] is True and calls["n"] == 1 and calls["confirm"] is True


def test_place_market_no_price_refuses(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    monkeypatch.setenv("WEBULL_TRADE_MAX_NOTIONAL", "500")
    monkeypatch.setattr(server, "_last_price", lambda s: None)  # snapshot lacks a usable price
    placed = _block_place(monkeypatch)
    out = json.loads(server.place("PLUG", "BUY", "1", "secret", "MARKET"))
    assert out["error"] == "CapUnverifiable" and placed["n"] == 0


def test_place_market_within_cap_submits(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    monkeypatch.setenv("WEBULL_TRADE_MAX_NOTIONAL", "500")
    monkeypatch.setattr(server, "_last_price", lambda s: 2.85)
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    calls = {"n": 0, "confirm": None}
    def fake(acct, order, confirm=False):
        calls["n"] += 1
        calls["confirm"] = confirm
        return {"submitted": True}
    monkeypatch.setattr(server.trading, "place", fake)
    out = json.loads(server.place("PLUG", "BUY", "1", "secret", "MARKET"))  # 1*2.85 <= 500
    assert out["placed"] is True and calls["n"] == 1 and calls["confirm"] is True


# --- auto-journal hook (real, after the gate) ---

# Captured before the conftest autouse fixture stubs server._journal_real_fills, so the swallow test
# exercises the REAL hook.
from webull_trade_mcp.server import _journal_real_fills as _real_trade_hook


def test_place_journals_after_successful_submit(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    monkeypatch.setenv("WEBULL_TRADE_MAX_NOTIONAL", "500")
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.trading, "place", lambda acct, order, confirm=False: {"submitted": True})
    seen = {"accts": None}

    def hook(accts):
        seen["accts"] = list(accts)
        return 1

    monkeypatch.setattr(server, "_journal_real_fills", hook)  # overrides the autouse no-op stub
    out = json.loads(server.place("PLUG", "BUY", "1", "secret", "LIMIT", "2.50"))
    assert out["placed"] is True and out["journaled"] == 1 and seen["accts"] == ["ACC"]


def test_bad_codeword_never_reaches_journal(monkeypatch):
    monkeypatch.setenv("WEBULL_TRADE_CODEWORD", "secret")
    _block_place(monkeypatch)
    seen = {"n": 0}
    monkeypatch.setattr(server, "_journal_real_fills", lambda a: seen.__setitem__("n", seen["n"] + 1) or 1)
    out = json.loads(server.place("PLUG", "BUY", "1", "wrong", "LIMIT", "2.50"))
    assert out["error"] == "BadCodeword" and seen["n"] == 0  # gate runs first; no journal


def test_journal_real_fills_swallows_errors(monkeypatch):
    from webull_web import journal_ingest

    def boom(*a, **k):
        raise RuntimeError("webull down")

    monkeypatch.setattr(journal_ingest, "sync_real", boom)
    assert _real_trade_hook(["ACC"]) == 0  # best-effort: never propagates


from webull_api.swing.screen import ScreenRow
from webull_api.swing.schema import SwingPlan


def _pass_plan(symbol="NVDA"):
    return SwingPlan(symbol=symbol, book=500, indicators={}, verdict="PASS",
                     entry=100.0, stop=95.0, target=115.0, shares=2,
                     actual_risk_pct=2.0, rr=3.0, exit_mode="A")


def _wire_screen(monkeypatch, rows, pending=None):
    monkeypatch.setenv("ORDER_INTENTS_DIR", "._unused")  # save_intent is mocked
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.swing_screen, "screen", lambda syms, book: rows)
    monkeypatch.setattr(server.intent_store, "list_intents",
                        lambda now: [{"symbol": s, "status": "pending"} for s in (pending or [])])
    saved = []
    monkeypatch.setattr(server.intent_store, "save_intent", lambda d: saved.append(d) or {**d, "id": "i"})
    monkeypatch.setattr(server.trading, "preview", lambda acct, o: {"estimated_cost": "200.60"})
    return saved


def test_screen_and_draft_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "screen_and_draft" in names


def test_screen_draft_drafts_pass_as_day_buystop(monkeypatch):
    saved = _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    out = json.loads(server.screen_draft(symbols="NVDA", book=500))
    assert [d["symbol"] for d in out["drafted"]] == ["NVDA"]
    assert len(saved) == 1
    o = saved[0]
    assert o["order_type"] == "STOP_LOSS_LIMIT" and o["side"] == "BUY"
    assert o["stop_price"] == "100.0" and o["limit_price"] == "100.3"
    assert o["quantity"] == "2" and o["time_in_force"] == "GTC" and o["source"] == "swing_screen"


def test_screen_draft_skips_non_pass(monkeypatch):
    saved = _wire_screen(monkeypatch, [ScreenRow(symbol="KO",
                         plan=SwingPlan(symbol="KO", book=500, indicators={}, verdict="SKIP", reason="no trigger"))])
    out = json.loads(server.screen_draft(symbols="KO"))
    assert out["drafted"] == [] and out["skipped"][0]["symbol"] == "KO" and saved == []


def test_screen_draft_dedups_pending(monkeypatch):
    saved = _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))], pending=["NVDA"])
    out = json.loads(server.screen_draft(symbols="NVDA"))
    assert out["drafted"] == [] and "already drafted" in out["skipped"][0]["reason"] and saved == []


def test_screen_draft_error_row(monkeypatch):
    _wire_screen(monkeypatch, [ScreenRow(symbol="X", error="bad bars")])
    out = json.loads(server.screen_draft(symbols="X"))
    assert out["errors"][0] == {"symbol": "X", "error": "bad bars"} and out["drafted"] == []


def test_screen_draft_never_places(monkeypatch):
    _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    server.screen_draft(symbols="NVDA")
    assert placed["n"] == 0


from webull_api.exits import ExitRow, ExitSignal


def _sig(symbol="AAPL", qty=10, last=270.0, reason="stop"):
    return ExitSignal(symbol=symbol, qty=qty, last=last, unrealized_pct=-10.0,
                      reason=reason, reasons=[reason], detail=f"{symbol} test detail")


def _wire_exits(monkeypatch, rows, pending=None):
    monkeypatch.setenv("ORDER_INTENTS_DIR", "._unused")  # save_intent is mocked
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.exits, "scan", lambda acct, cfg=None: rows)
    monkeypatch.setattr(server.intent_store, "list_intents", lambda now: list(pending or []))
    saved = []
    monkeypatch.setattr(server.intent_store, "save_intent", lambda d: saved.append(d) or {**d, "id": "i"})
    monkeypatch.setattr(server.trading, "preview", lambda acct, o: {"estimated_cost": "2700.00"})
    return saved


def test_manage_exits_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "manage_exits" in names


def test_exits_draft_drafts_full_close_sell_limit(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig("AAPL", 10, 270.0, "stop"))])
    out = json.loads(server.exits_draft())
    assert [d["symbol"] for d in out["drafted"]] == ["AAPL"]
    assert len(saved) == 1
    o = saved[0]
    assert o["side"] == "SELL" and o["order_type"] == "LIMIT"
    assert o["quantity"] == "10" and o["limit_price"] == "270.0" and o["time_in_force"] == "DAY"
    assert o["source"] == "exit_manager" and o["stop_price"] is None
    assert o["thesis"].startswith("Exit (stop)")


def test_exits_draft_held_when_no_signal(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="KO", qty=5, signal=None, held="no exit signal")])
    out = json.loads(server.exits_draft())
    assert out["drafted"] == [] and out["held"][0] == {"symbol": "KO", "reason": "no exit signal"}
    assert saved == []


def test_exits_draft_dedups_pending_sell(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig())],
                        pending=[{"symbol": "AAPL", "side": "SELL"}])
    out = json.loads(server.exits_draft())
    assert out["drafted"] == [] and "already drafted" in out["held"][0]["reason"] and saved == []


def test_exits_draft_pending_buy_does_not_suppress(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig())],
                        pending=[{"symbol": "AAPL", "side": "BUY"}])
    out = json.loads(server.exits_draft())
    assert [d["symbol"] for d in out["drafted"]] == ["AAPL"] and len(saved) == 1


def test_exits_draft_no_price_is_held(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig(last=None))])
    out = json.loads(server.exits_draft())
    assert out["drafted"] == [] and out["held"][0]["reason"] == "no price" and saved == []


def test_exits_draft_error_row(monkeypatch):
    _wire_exits(monkeypatch, [ExitRow(symbol="X", error="boom")])
    out = json.loads(server.exits_draft())
    assert out["errors"][0] == {"symbol": "X", "error": "boom"} and out["drafted"] == []


def test_manage_exits_never_places(monkeypatch):
    _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig())])
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    server.exits_draft()
    assert placed["n"] == 0


from datetime import datetime as _dt


def test_screen_draft_respects_ttl_min(monkeypatch):
    saved = _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    server.screen_draft(symbols="NVDA", ttl_min=120)
    o = saved[0]
    delta = _dt.fromisoformat(o["expires_at"]) - _dt.fromisoformat(o["created_at"])
    assert delta.total_seconds() == 120 * 60


def test_screen_draft_entries_are_gtc_and_write_a_plan(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "i"))
    monkeypatch.setenv("POSITION_PLANS_DIR", str(tmp_path / "pp"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    monkeypatch.setattr(server.trading, "preview", lambda acct, order, **kw: {"ok": True})

    class _Plan:
        verdict = "PASS"
        symbol = "AMD"
        entry = 10.20
        stop = 9.10
        target = 12.40
        shares = 5
        rr = 2.1
        actual_risk_pct = 1.0
        exit_mode = "A"
        reason = ""

    class _Row:
        symbol = "AMD"
        error = None
        plan = _Plan()

    monkeypatch.setattr(server.swing_screen, "screen", lambda universe, book: [_Row()])

    res = json.loads(server.screen_draft(symbols="AMD", book=400))
    assert len(res["drafted"]) == 1
    intent = next(i for i in server.intent_store.list_intents(server._now().isoformat())
                  if i["symbol"] == "AMD" and i["side"] == "BUY")
    assert intent["time_in_force"] == "GTC"                 # was DAY
    plan = server.position_plans.get_plan("AMD")
    assert plan["structural_stop"] == 9.10 and plan["target"] == 12.40 and plan["entry"] == 10.20
    assert plan["shares"] == 5 and plan["book"] == 400 and plan["source"] == "swing_screen"
    assert plan["created_at"]


def test_exits_draft_respects_ttl_min(monkeypatch):
    saved = _wire_exits(monkeypatch, [ExitRow(symbol="AAPL", qty=10, signal=_sig())])
    server.exits_draft(ttl_min=120)
    o = saved[0]
    delta = _dt.fromisoformat(o["expires_at"]) - _dt.fromisoformat(o["created_at"])
    assert delta.total_seconds() == 120 * 60


def test_morning_routine_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "morning_routine" in names


def test_morning_routine_runs_both_and_merges(monkeypatch):
    calls = {"discover": 0, "exits": 0}

    def fake_discover(book=500.0, extra_symbols="", ttl_min=None):
        calls["discover"] += 1
        return json.dumps({"drafted": [{"symbol": "NVDA"}], "skipped": [], "errors": []})

    def fake_exits(account_id="", stop_loss_pct=8.0, take_profit_pct=20.0, ttl_min=None):
        calls["exits"] += 1
        return json.dumps({"drafted": [{"symbol": "AAPL"}, {"symbol": "TSLA"}], "held": [], "errors": []})

    monkeypatch.setattr(server, "discover_draft", fake_discover)
    monkeypatch.setattr(server, "exits_draft", fake_exits)
    monkeypatch.setattr(server, "protect",
                        lambda *a, **k: json.dumps({"drafted": [], "needs_manual": [], "protected": [], "errors": []}))
    out = json.loads(server.morning_routine_run())
    assert calls == {"discover": 1, "exits": 1}   # entries come from DISCOVERY, not the watchlist
    assert [d["symbol"] for d in out["entries"]["drafted"]] == ["NVDA"]
    assert [d["symbol"] for d in out["exits"]["drafted"]] == ["AAPL", "TSLA"]
    assert "1 entry draft(s) + 2 exit draft(s)" in out["message"]


def test_morning_routine_defaults_to_discovery_not_watchlist(monkeypatch):
    # No `symbols` -> the routine must DISCOVER (scan), never touch screen_draft (the watchlist path).
    used = {"discover": 0, "screen": 0}
    monkeypatch.setattr(server, "discover_draft",
                        lambda **k: used.__setitem__("discover", used["discover"] + 1) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "screen_draft",
                        lambda *a, **k: used.__setitem__("screen", used["screen"] + 1) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "exits_draft", lambda *a, **k: json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "protect", lambda *a, **k: json.dumps({"drafted": []}))
    server.morning_routine_run()
    assert used == {"discover": 1, "screen": 0}


def test_morning_routine_symbols_override_screens(monkeypatch):
    # An explicit `symbols` list overrides discovery and screens exactly those names.
    used = {"discover": 0, "screen": 0}
    monkeypatch.setattr(server, "discover_draft",
                        lambda **k: used.__setitem__("discover", used["discover"] + 1) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "screen_draft",
                        lambda *a, **k: used.__setitem__("screen", used["screen"] + 1) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "exits_draft", lambda *a, **k: json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "protect", lambda *a, **k: json.dumps({"drafted": []}))
    server.morning_routine_run(symbols="NVDA")
    assert used == {"discover": 0, "screen": 1}


def test_morning_routine_uses_routine_ttl(monkeypatch):
    seen = {}
    monkeypatch.setattr(server, "discover_draft",
                        lambda book=500.0, extra_symbols="", ttl_min=None:
                        seen.__setitem__("discover", ttl_min) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "exits_draft",
                        lambda account_id="", stop_loss_pct=8.0, take_profit_pct=20.0, ttl_min=None:
                        seen.__setitem__("exits", ttl_min) or json.dumps({"drafted": []}))
    monkeypatch.setattr(server, "protect",
                        lambda *a, **k: json.dumps({"drafted": [], "needs_manual": [], "protected": [], "errors": []}))
    server.morning_routine_run()
    assert seen["discover"] == server._ROUTINE_TTL_MIN and seen["exits"] == server._ROUTINE_TTL_MIN


def test_morning_routine_handles_subcall_error(monkeypatch):
    monkeypatch.setattr(server, "discover_draft",
                        lambda *a, **k: json.dumps({"error": "MarketDataNotEntitled", "message": "x"}))
    ran = {"exits": 0}

    def fake_exits(*a, **k):
        ran["exits"] += 1
        return json.dumps({"drafted": [{"symbol": "AAPL"}]})

    monkeypatch.setattr(server, "exits_draft", fake_exits)
    monkeypatch.setattr(server, "protect",
                        lambda *a, **k: json.dumps({"drafted": [], "needs_manual": [], "protected": [], "errors": []}))
    out = json.loads(server.morning_routine_run())
    assert ran["exits"] == 1
    assert out["entries"]["error"] == "MarketDataNotEntitled"
    assert [d["symbol"] for d in out["exits"]["drafted"]] == ["AAPL"]
    assert "0 entry draft(s) + 1 exit draft(s)" in out["message"]


def test_morning_routine_never_places(monkeypatch):
    # Run the REAL screen_draft + exits_draft (engines wired) through morning_routine_run; place must
    # never be reached.
    monkeypatch.setenv("ORDER_INTENTS_DIR", "._unused")
    monkeypatch.setattr(server, "_default_account", lambda a: "ACC")
    monkeypatch.setattr(server.swing_screen, "screen",
                        lambda syms, book: [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    monkeypatch.setattr(server.exits, "scan",
                        lambda acct, cfg=None: [ExitRow(symbol="AAPL", qty=10, signal=_sig())])
    monkeypatch.setattr(server.intent_store, "list_intents", lambda now: [])
    monkeypatch.setattr(server.intent_store, "save_intent", lambda d: {**d, "id": "i"})
    monkeypatch.setattr(server.trading, "preview", lambda acct, o: {"estimated_cost": "1"})
    monkeypatch.setattr(server, "protect",
                        lambda *a, **k: json.dumps({"drafted": [], "needs_manual": [], "protected": [], "errors": []}))
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    server.morning_routine_run(symbols="NVDA")
    assert placed["n"] == 0


def test_morning_routine_includes_protection_and_never_places(monkeypatch):
    monkeypatch.setattr(server, "screen_draft",
                        lambda *a, **k: json.dumps({"drafted": [], "skipped": [], "errors": []}))
    monkeypatch.setattr(server, "exits_draft",
                        lambda *a, **k: json.dumps({"drafted": [], "held": [], "errors": []}))
    monkeypatch.setattr(server, "protect",
                        lambda *a, **k: json.dumps({"drafted": [{"symbol": "AMD", "stop": 9.0,
                                                    "source": "structural", "qty": "5"}],
                                                    "needs_manual": [], "protected": [], "errors": []}))
    def _boom(*a, **k):
        raise AssertionError("morning_routine must never place")
    monkeypatch.setattr(server.trading, "place", _boom)

    res = json.loads(server.morning_routine_run(symbols="AMD", book=400))
    assert res["protection"]["drafted"][0]["symbol"] == "AMD"
    assert "protection" in res and "entries" in res and "exits" in res


# ── discover_and_draft: Claude sources its own candidates (not a watchlist) ──

from webull_api.discovery import DiscoverResult


def _wire_discover(monkeypatch, found):
    monkeypatch.setattr(server.discovery, "discover", lambda **k: found)


def test_discover_and_draft_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "discover_and_draft" in names


def test_discover_and_draft_drafts_discovered_pass(monkeypatch):
    _wire_discover(monkeypatch, DiscoverResult(symbols=["NVDA"], scanned=180, in_band=1))
    saved = _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    out = json.loads(server.discover_and_draft(book=500))
    assert [d["symbol"] for d in out["drafted"]] == ["NVDA"]
    assert out["discovered"] == ["NVDA"] and out["scanned"] == 180 and out["in_band"] == 1
    assert len(saved) == 1 and saved[0]["source"] == "swing_screen"


def test_discover_and_draft_empty_when_none_in_band(monkeypatch):
    _wire_discover(monkeypatch, DiscoverResult(symbols=[], scanned=180, in_band=0))
    out = json.loads(server.discover_and_draft())
    assert out["drafted"] == [] and out["discovered"] == [] and out["scanned"] == 180


def test_discover_and_draft_never_places(monkeypatch):
    _wire_discover(monkeypatch, DiscoverResult(symbols=["NVDA"], scanned=180, in_band=1))
    _wire_screen(monkeypatch, [ScreenRow(symbol="NVDA", plan=_pass_plan("NVDA"))])
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    server.discover_and_draft()
    assert placed["n"] == 0


def test_discover_ceiling_tracks_book(monkeypatch):
    # The effective upper band = min(max_price, 0.20*book), matching the planner's price ceiling.
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return DiscoverResult(symbols=[], scanned=0, in_band=0)

    monkeypatch.setattr(server.discovery, "discover", capture)
    server.discover_and_draft(book=400)                 # planner ceiling 0.20*400 = 80 < default 100
    assert seen["max_price"] == 80.0
    server.discover_and_draft(book=400, max_price=50)   # tighter manual cap wins
    assert seen["max_price"] == 50.0


# ── protect_positions: draft a resting GTC protective stop for unprotected positions ──


def test_protect_positions_drafts_gtc_sell_stop_and_never_places(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    # one unprotected position with a plan-of-record structural stop
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans",
                        lambda: {"AMD": {"symbol": "AMD", "structural_stop": 9.0}})
    monkeypatch.setattr(reconcile, "scan_unprotected",
                        lambda acct, **kw: [reconcile.Unprotected(
                            "AMD", 5, 10.0, 10.4, 9.0, "structural",
                            reconcile.protective_order("AMD", 5, 9.0))])
    monkeypatch.setattr(server.trading, "preview", lambda acct, order, **kw: {"ok": True})
    # hard guard: placement must never be reached
    placed = {"n": 0}
    def _count_place(*a, **k):
        placed["n"] += 1
    monkeypatch.setattr(server.trading, "place", _count_place)

    res = json.loads(server.protect("", 8.0))
    assert len(res["drafted"]) == 1
    d = res["drafted"][0]
    assert d["symbol"] == "AMD" and d["source"] == "structural"
    # the written intent is a GTC SELL STOP_LOSS (market-on-trigger)
    saved = server.intent_store.list_intents(server._now().isoformat())
    intent = next(i for i in saved if i["symbol"] == "AMD")
    assert intent["side"] == "SELL" and intent["order_type"] == "STOP_LOSS"
    assert intent["time_in_force"] == "GTC" and intent["source"] == "position_protect"
    assert placed["n"] == 0


def test_protect_positions_isolates_a_preview_failure(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "iso"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans", lambda: {})
    rows = [
        reconcile.Unprotected("BAD", 5, 10.0, 10.4, 9.0, "structural", reconcile.protective_order("BAD", 5, 9.0)),
        reconcile.Unprotected("GOOD", 3, 20.0, 20.4, 18.4, "backstop", reconcile.protective_order("GOOD", 3, 18.4)),
    ]
    monkeypatch.setattr(reconcile, "scan_unprotected", lambda acct, **kw: rows)
    def _preview(acct, order, **kw):
        if order["symbol"] == "BAD":
            raise RuntimeError("preview blew up")
        return {"ok": True}
    monkeypatch.setattr(server.trading, "preview", _preview)
    res = json.loads(server.protect("", 8.0))
    assert any(e["symbol"] == "BAD" for e in res["errors"])
    assert any(d["symbol"] == "GOOD" for d in res["drafted"])


def test_protect_positions_reports_needs_manual(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents2"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans", lambda: {})
    monkeypatch.setattr(reconcile, "scan_unprotected",
                        lambda acct, **kw: [reconcile.Unprotected(
                            "AMD", 5, None, None, None, "manual", None)])
    res = json.loads(server.protect("", 8.0))
    assert res["drafted"] == []
    assert res["needs_manual"][0]["symbol"] == "AMD"


def test_protect_positions_needs_manual_reports_fractional_skip_reason(monkeypatch, tmp_path):
    # A fractional row's protective is always None (Webull won't rest a stop on it) — the
    # accurate reason lives on skip_reason, not the generic "no structural stop..." wording.
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents_frac"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans", lambda: {})
    monkeypatch.setattr(reconcile, "scan_unprotected",
                        lambda acct, **kw: [reconcile.Unprotected(
                            "FBTC", 1.7, 55.48, 57.31, 52.0, "backstop", None,
                            fractional=True, skip_reason="fractional: monitored, above stop")])
    res = json.loads(server.protect("", 8.0))
    assert res["needs_manual"] == [{"symbol": "FBTC",
                                    "reason": "fractional: monitored, above stop"}]


def test_protect_positions_routes_error_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents3"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans", lambda: {})
    monkeypatch.setattr(reconcile, "scan_unprotected",
                        lambda acct, **kw: [reconcile.Unprotected(
                            "AMD", 5, None, None, None, "error", None, error="boom")])
    res = json.loads(server.protect("", 8.0))
    assert res["drafted"] == [] and res["needs_manual"] == []
    assert res["errors"] == [{"symbol": "AMD", "error": "boom"}]


def test_protect_positions_dedups_pending_sell(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "intents_ded"))
    monkeypatch.setattr(server, "_default_account", lambda a: "ACCT")
    from webull_api import reconcile
    monkeypatch.setattr(server.position_plans, "all_plans",
                        lambda: {"AMD": {"symbol": "AMD", "structural_stop": 9.0}})
    monkeypatch.setattr(reconcile, "scan_unprotected",
                        lambda acct, **kw: [reconcile.Unprotected(
                            "AMD", 5, 10.0, 10.4, 9.0, "structural",
                            reconcile.protective_order("AMD", 5, 9.0))])
    monkeypatch.setattr(server.trading, "preview", lambda acct, order, **kw: {"ok": True})
    # a pending SELL intent for AMD already exists
    server.intent_store.save_intent({
        "symbol": "AMD", "side": "SELL", "status": "pending",
        "expires_at": (server._now() + __import__("datetime").timedelta(minutes=30)).isoformat(),
    })
    import json
    res = json.loads(server.protect("", 8.0))
    assert res["drafted"] == []
    assert any(p["symbol"] == "AMD" for p in res["protected"])


def test_protect_positions_tool_registered():
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "protect_positions" in names


# ── annotate_intent: write a red-team verdict + bear case onto a pending draft ──

def test_annotate_intent_writes_bear_case_and_never_places(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "rt"))
    # a pending BUY intent to annotate
    server.intent_store.save_intent({
        "id": "amd1", "symbol": "AMD", "side": "BUY", "status": "pending",
        "created_at": server._now().isoformat(),
        "expires_at": (server._now() + __import__("datetime").timedelta(minutes=30)).isoformat(),
    })
    placed = {"n": 0}
    monkeypatch.setattr(server.trading, "place", lambda *a, **k: placed.__setitem__("n", placed["n"] + 1))
    res = json.loads(server.annotate_pending("AMD", "caution", "earnings in 4 days; RS laggard"))
    assert res["annotated"]["red_team_verdict"] == "caution"
    assert res["annotated"]["bear_case"] == "earnings in 4 days; RS laggard"
    assert res["annotated"].get("red_team_at")
    assert placed["n"] == 0   # never places


def test_annotate_intent_rejects_bad_verdict(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "rt2"))
    res = json.loads(server.annotate_pending("AMD", "sell-it", "bad"))
    assert res["error"] == "BadVerdict"


def test_annotate_intent_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("ORDER_INTENTS_DIR", str(tmp_path / "rt3"))
    res = json.loads(server.annotate_pending("NOPE", "kill", "no such draft"))
    assert res["error"] == "IntentNotFound"


def test_annotate_intent_registered():
    # matches this file's existing idiom (asyncio is imported at the top of the module)
    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert "annotate_intent" in names
