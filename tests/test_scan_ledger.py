"""scan_ledger_service — snapshot pass, scoring pass, idempotency, suite order."""
from webull_web import scan_ledger_service, scanner_store
from webull_web.runner_cli import PAPER_SUITE


def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("WEBULL_SCANNER_DIR", str(tmp_path / "scanner"))
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "activity"))


def _fake_scan(**kw):
    return {"source": {"kind": "discovery"}, "watchlist": None, "scanned": 2, "errors": [],
            "setups": [
                {"symbol": "AAPL", "last": 100.0, "score": 3, "change_pct": 1.2,
                 "tags": [{"kind": "dip_buy", "label": "Dip buy", "detail": ""}]},
                {"symbol": "MSFT", "last": 50.0, "score": 2, "change_pct": -0.5,
                 "tags": [{"kind": "momentum_leader", "label": "Momentum", "detail": ""}]},
            ]}


def _flat_bars(dates, price):
    return [{"time": d, "open": price, "high": price, "low": price, "close": price,
             "volume": 1000} for d in dates]


def test_run_snapshots_ranked_picks(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(scan_ledger_service.setups_service, "scan_setups", _fake_scan)
    monkeypatch.setattr(scan_ledger_service, "_daily_bars", lambda sym: [])
    report = scan_ledger_service.run(force=True)
    assert report["result"] == "ok"
    snaps = scanner_store.load_snapshots()
    assert {s["symbol"] for s in snaps} == {"AAPL", "MSFT"}
    assert snaps[0]["tags"] == ["dip_buy"] and snaps[0]["source_kind"] == "discovery"
    # same-day guard: a second run without force is a no_op
    assert scan_ledger_service.run(force=False)["result"] == "no_op"
    # forced re-run appends nothing new (dedup by id)
    scan_ledger_service.run(force=True)
    assert len(scanner_store.load_snapshots()) == 2


def test_run_scores_matured_snapshots(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(scan_ledger_service.setups_service, "scan_setups",
                        lambda **kw: {"source": {"kind": "discovery"}, "setups": [], "errors": []})
    scanner_store.append_snapshots([
        {"id": "2026-07-01:AAPL", "date": "2026-07-01", "symbol": "AAPL",
         "score": 3, "tags": ["dip_buy"], "last": 100.0}])
    dates = [f"2026-07-{d:02d}" for d in (1, 2, 3, 6, 7, 8, 9, 10)]
    monkeypatch.setattr(scan_ledger_service, "_daily_bars",
                        lambda sym: _flat_bars(dates, 100.0 if sym == "SPY" else 50.0))
    report = scan_ledger_service.run(force=True)
    assert report["result"] == "ok"
    scores = scanner_store.load_scores()
    assert [s["id"] for s in scores] == ["2026-07-01:AAPL:5"]     # 10/20d not matured
    assert scores[0]["excess_bp"] == 0.0                          # both flat
    # idempotent: a forced second run adds nothing
    scan_ledger_service.run(force=True)
    assert len(scanner_store.load_scores()) == 1


def test_soft_error_symbol_is_skipped_and_reported(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(scan_ledger_service.setups_service, "scan_setups",
                        lambda **kw: {"source": {"kind": "discovery"}, "setups": [], "errors": []})
    scanner_store.append_snapshots([
        {"id": "2026-07-01:BAD", "date": "2026-07-01", "symbol": "BAD",
         "score": 1, "tags": [], "last": 1.0}])
    dates = [f"2026-07-{d:02d}" for d in (1, 2, 3, 6, 7, 8)]

    def bars(sym):
        if sym == "BAD":
            raise RuntimeError("no bars")
        return _flat_bars(dates, 100.0)

    monkeypatch.setattr(scan_ledger_service, "_daily_bars", bars)
    report = scan_ledger_service.run(force=True)
    assert report["result"] == "partial"
    assert any("BAD" in e for e in report["errors"])
    assert scanner_store.load_scores() == []
