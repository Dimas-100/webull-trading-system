"""exec_quality_service.view."""


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBULL_EXEC_DIR", str(tmp_path / "exec"))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))


def test_view_joins_decisions_with_real_fills(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from webull_api import exec_ledger
    from webull_api.journal.schema import Fill
    from webull_web import exec_quality_service, journal_store
    exec_ledger.append([
        {"id": "A", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
         "limit_price": None, "stop_price": None, "mark": 100.0},
        {"id": "B", "symbol": "MSFT", "side": "BUY", "order_type": "MARKET",
         "limit_price": None, "stop_price": None, "mark": 50.0},
    ])
    journal_store.append_fills([Fill(
        id="A", source="real", account_id="ACC", symbol="AAPL", side="BUY",
        quantity=1, price=100.1, filled_at_iso="2026-07-21T16:00:00", order_type="MARKET")])
    out = exec_quality_service.view()
    assert out["summary"]["fills_matched"] == 1
    assert out["summary"]["awaiting_fill"] == 1
    assert out["rows"][0]["signed_bp"] == 10.0


def test_view_empty_stores_zeroed(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    from webull_web import exec_quality_service
    out = exec_quality_service.view()
    assert out["summary"]["fills_matched"] == 0 and out["rows"] == []
