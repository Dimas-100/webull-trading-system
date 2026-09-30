"""Owner cash-flow auto-detection: flow = Δcash between cash-bearing snapshots + net traded
notional in the window. Appends to the contributions ledger; never fabricates on a missing
baseline; read-only toward the broker (order history only)."""
import inspect
import json
from datetime import datetime, timedelta

from webull_web import flows_service as svc
from webull_web import contributions_store, netliq_store, paper_service


def _seed_rows(monkeypatch, tmp_path, prev_cash, cur_cash, prev_days_ago=1):
    """Two cash-bearing netliq rows: one earlier, one dated today (the run's own ET day)."""
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    iso, today = paper_service.now_et()
    cur_dt = datetime.fromisoformat(iso)
    prev_dt = cur_dt - timedelta(days=prev_days_ago)
    netliq_store.append({"date": prev_dt.date().isoformat(), "ts": prev_dt.isoformat(),
                         "real": prev_cash, "real_cash": prev_cash,
                         "paper_equity": 1.0, "paper_options": 1.0})
    netliq_store.append({"date": today, "ts": iso, "real": cur_cash, "real_cash": cur_cash,
                         "paper_equity": 1.0, "paper_options": 1.0})
    return prev_dt, cur_dt


def _history(fills):
    """Order-history payload in the live combo-wrapper shape."""
    return [{"orders": [f]} for f in fills]


def _fill(side, qty, price, at, oid="o1", status="FILLED"):
    return {"client_order_id": oid, "symbol": "AAPL", "side": side, "status": status,
            "filled_quantity": str(qty), "filled_price": str(price), "filled_time_at": at}


def test_deposit_detected_and_recorded(tmp_path, monkeypatch):
    prev_dt, cur_dt = _seed_rows(monkeypatch, tmp_path, prev_cash=311.41, cur_cash=308.41)
    fill_at = (prev_dt + (cur_dt - prev_dt) / 2).isoformat()
    rep = svc.run(history_fn=lambda aid: _history([_fill("BUY", 1, 303.00, fill_at)]),
                  account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "+300.00" in rep["summary"]
    entries = contributions_store.load()
    assert len(entries) == 1
    assert entries[0]["amount"] == 300.0 and "auto-detected" in entries[0]["note"]


def test_trades_fully_explain_cash_change(tmp_path, monkeypatch):
    prev_dt, cur_dt = _seed_rows(monkeypatch, tmp_path, prev_cash=500.0, cur_cash=197.0)
    fill_at = (prev_dt + (cur_dt - prev_dt) / 2).isoformat()
    rep = svc.run(history_fn=lambda aid: _history([_fill("BUY", 1, 303.00, fill_at)]),
                  account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "no external flow" in rep["summary"]
    assert contributions_store.load() == []


def test_withdrawal_is_negative(tmp_path, monkeypatch):
    _seed_rows(monkeypatch, tmp_path, prev_cash=500.0, cur_cash=450.0)
    rep = svc.run(history_fn=lambda aid: [], account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "-50.00" in rep["summary"]
    assert contributions_store.load()[0]["amount"] == -50.0


def test_sell_proceeds_are_not_a_deposit(tmp_path, monkeypatch):
    prev_dt, cur_dt = _seed_rows(monkeypatch, tmp_path, prev_cash=100.0, cur_cash=403.0)
    fill_at = (prev_dt + (cur_dt - prev_dt) / 2).isoformat()
    rep = svc.run(history_fn=lambda aid: _history([_fill("SELL", 1, 303.00, fill_at)]),
                  account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "no external flow" in rep["summary"]
    assert contributions_store.load() == []


def test_fills_outside_window_ignored(tmp_path, monkeypatch):
    prev_dt, _cur = _seed_rows(monkeypatch, tmp_path, prev_cash=300.0, cur_cash=500.0)
    old_fill = _fill("BUY", 1, 303.00, (prev_dt - timedelta(days=2)).isoformat(), oid="old")
    rep = svc.run(history_fn=lambda aid: _history([old_fill]), account_fn=lambda: "A1")
    assert "+200.00" in rep["summary"]          # Δcash only; the stale fill contributes nothing
    assert contributions_store.load()[0]["amount"] == 200.0


def test_needs_two_cash_bearing_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    iso, today = paper_service.now_et()
    netliq_store.append({"date": today, "ts": iso, "real": 100.0, "real_cash": 100.0,
                         "paper_equity": 1.0, "paper_options": 1.0})
    rep = svc.run(history_fn=lambda aid: [], account_fn=lambda: "A1")
    # An expected state, not an error: reported ok (and run-log-stamped, so the watchdog
    # doesn't page over a still-accumulating cash trail), but nothing is ever fabricated.
    assert rep["result"] == "ok" and "baseline" in rep["summary"]
    assert contributions_store.load() == []


def test_cashless_rows_are_no_baseline(tmp_path, monkeypatch):
    """Pre-feature history rows (no real_cash) never serve as a diff baseline."""
    _seed_rows(monkeypatch, tmp_path, prev_cash=100.0, cur_cash=100.0)
    rows = netliq_store.load()
    (tmp_path / "netliq_history.jsonl").write_text(
        "\n".join(json.dumps({k: v for k, v in r.items() if k != "real_cash"} if i == 0 else r)
                  for i, r in enumerate(rows)) + "\n", encoding="utf-8")
    rep = svc.run(history_fn=lambda aid: [], account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "baseline" in rep["summary"]
    assert contributions_store.load() == []


def test_idempotent_within_a_day(tmp_path, monkeypatch):
    _seed_rows(monkeypatch, tmp_path, prev_cash=100.0, cur_cash=300.0)
    assert svc.run(history_fn=lambda aid: [], account_fn=lambda: "A1")["result"] == "ok"
    assert svc.run(history_fn=lambda aid: [], account_fn=lambda: "A1")["result"] == "no_op"
    rep = svc.run(force=True, history_fn=lambda aid: [], account_fn=lambda: "A1")
    assert rep["result"] == "ok" and "already recorded" in rep["summary"]
    assert len(contributions_store.load()) == 1   # force never double-appends the same day


def test_broker_failure_is_an_error_not_a_guess(tmp_path, monkeypatch):
    def boom(aid):
        raise RuntimeError("HTTP Status: 429, Code: TOO_MANY_REQUESTS")
    _seed_rows(monkeypatch, tmp_path, prev_cash=100.0, cur_cash=300.0)
    rep = svc.run(history_fn=boom, account_fn=lambda: "A1")
    assert rep["result"] == "error"
    assert contributions_store.load() == []


def test_registered_right_after_netliq_in_the_suite():
    from webull_web import runner_cli
    mods = [m for _, m in runner_cli.PAPER_SUITE]
    assert mods.index("flows_service") == mods.index("netliq_snapshot_service") + 2


def test_flows_surface_has_no_submit_path():
    src = inspect.getsource(svc)
    for forbidden in ("trading.place", "place_order", "confirm=True"):
        assert forbidden not in src, f"flows_service must not contain {forbidden!r}"
