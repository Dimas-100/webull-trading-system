import json

import webull_trade_mcp.server as srv


def test_autopilot_run_tool_delegates(monkeypatch):
    captured = {}
    def fake_run(book=400.0, account_id=None, **kw):
        captured["book"], captured["account_id"] = book, account_id
        return {"placed": [], "skipped": [], "errors": [], "message": "ok"}
    monkeypatch.setattr(srv.autopilot_run_impl, "run", fake_run)
    out = json.loads(srv.autopilot_run_tool(book=400.0, account_id="ACC"))
    assert out["message"] == "ok"
    assert captured["book"] == 400.0
    assert captured["account_id"] == "ACC"
