"""Daily net-liq snapshot runner: one JSONL line per ET day (net-liq + real-book cash);
null for an unvaluable book, nothing recorded when NO book can be valued; registered as
the 8th of the thirteen suite steps."""
import inspect
import json

from webull_web import netliq_snapshot_service as svc


def _patch_books(monkeypatch, real=400.0, pe=2500.0, po=1000.0, cash=380.0):
    monkeypatch.setattr(svc, "best_real_account",
                        lambda: (None, None, None) if real is None else ("A1", real, cash))
    monkeypatch.setattr(svc, "_paper_equity_net_liq", lambda: pe)
    monkeypatch.setattr(svc, "_paper_options_net_liq", lambda: po)


def _lines(tmp_path):
    f = tmp_path / "netliq_history.jsonl"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def test_appends_one_line_per_run(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    _patch_books(monkeypatch)
    rep = svc.run()
    assert rep["result"] == "ok" and rep["errors"] == []
    rows = [json.loads(l) for l in _lines(tmp_path)]
    assert len(rows) == 1
    assert rows[0]["real"] == 400.0 and rows[0]["paper_equity"] == 2500.0 and rows[0]["paper_options"] == 1000.0
    assert rows[0]["real_cash"] == 380.0        # the flows step's deposit-detection baseline
    assert rows[0]["date"] and rows[0]["ts"]


def test_same_day_guard_and_force(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    _patch_books(monkeypatch)
    assert svc.run()["result"] == "ok"
    assert svc.run()["result"] == "no_op"          # guarded
    assert len(_lines(tmp_path)) == 1
    assert svc.run(force=True)["result"] == "ok"   # forced re-run appends
    assert len(_lines(tmp_path)) == 2


def test_unvaluable_book_is_null_never_fabricated(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    _patch_books(monkeypatch, real=None)
    rep = svc.run()
    assert rep["result"] == "ok" and any("real" in e for e in rep["errors"])  # soft error, still records
    row = json.loads(_lines(tmp_path)[0])
    assert row["real"] is None and row["paper_equity"] == 2500.0


def test_real_book_rides_out_broker_throttling(monkeypatch):
    # The suite's earlier bar fetches can exhaust the API quota; the real-book read must retry
    # through a 429 instead of recording null every night.
    from webull_api import portfolio
    calls = []

    def throttled_balance(aid):
        calls.append(aid)
        if len(calls) < 3:
            raise Exception("HTTP Status: 429, Code: TOO_MANY_REQUESTS, Msg: Too many requests")
        return {"total_net_liquidation_value": "1234.5", "total_cash_balance": "25.5"}

    monkeypatch.setattr(portfolio, "list_accounts", lambda: [{"account_id": "A1"}])
    monkeypatch.setattr(portfolio, "get_balance", throttled_balance)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert svc.best_real_account() == ("A1", 1234.5, 25.5)


def test_all_books_down_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    _patch_books(monkeypatch, real=None, pe=None, po=None)
    rep = svc.run()
    assert rep["result"] == "error"
    assert _lines(tmp_path) == []


def test_registered_first_of_the_three_suite_steps():
    # SIMPLIFIED 2026-09-29 (owner: RSI2-only): net-liq feeds RSI2-real's sizing and flows' cash
    from webull_web import runner_cli
    assert runner_cli.PAPER_SUITE == [
        ("Net-liq", "netliq_snapshot_service"),
        ("RSI2-real", "rsi2_real_service"),
        ("Flows", "flows_service"),
    ]
    parked = [m for _l, m in runner_cli.PARKED_SUITE]
    assert parked == ["rsi2_service"]      # the rest were archived 2026-09-29 (docs/ARCHIVE.md)
    assert not set(parked) & {m for _l, m in runner_cli.PAPER_SUITE}


def test_netliq_surface_has_no_submit_path():
    from webull_web import netliq_store
    for mod in (svc, netliq_store):
        src = inspect.getsource(mod)
        for forbidden in ("trading.place", "import trading", "place_order", "confirm=True"):
            assert forbidden not in src, f"{mod.__name__} must not contain {forbidden!r}"


def test_store_load_empty(tmp_path, monkeypatch):
    from webull_web import netliq_store
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    assert netliq_store.load() == []


def test_store_load_sorts_by_date(tmp_path, monkeypatch):
    from webull_web import netliq_store
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    netliq_store.append({"date": "2026-07-03", "real": 2.0})
    netliq_store.append({"date": "2026-07-01", "real": 1.0})
    assert [r["date"] for r in netliq_store.load()] == ["2026-07-01", "2026-07-03"]


def test_store_load_skips_corrupt_lines(tmp_path, monkeypatch):
    from webull_web import netliq_store
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    netliq_store.append({"date": "2026-07-01", "real": 1.0})
    with (tmp_path / "netliq_history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{not json}\n\n[1,2]\n")
    rows = netliq_store.load()
    assert len(rows) == 1 and rows[0]["date"] == "2026-07-01"
