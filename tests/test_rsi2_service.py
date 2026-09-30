import json
from webull_api.strategy import rsi2 as rsi2_engine
from webull_web import rsi2_service as svc


def _raw(closes_newest_first, date):
    return [{"time": date, "open": c, "high": c, "low": c, "close": c} for c in closes_newest_first]


def _seed(tmp_path, monkeypatch, *, today="2026-07-08", positions=None, cash=79000.0, lots=None,
          stub_settle=True):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path / "paper"))
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path / "activity"))
    monkeypatch.setenv("RSI2_STATE_DIR", str(tmp_path / "activity"))
    (tmp_path / "paper").mkdir()
    (tmp_path / "paper" / "default.json").write_text(json.dumps({
        "account_id": "default", "starting_cash": 100000.0, "cash": cash,
        "positions": positions or {}, "open_orders": [], "history": [],
        "realized_pnl": 0.0, "created_at": today, "updated_at": today}), encoding="utf-8")
    if lots is not None:
        (tmp_path / "activity").mkdir(exist_ok=True)
        (tmp_path / "activity" / "rsi2_state.json").write_text(
            json.dumps({"schema_version": 1, "owned_lots": lots, "pending_orders": [],
                        "reconciliation_log": [], "updated_at": None}), encoding="utf-8")
    # shrink the universe + stub the market + freeze the clock
    monkeypatch.setattr(svc, "CFG", rsi2_engine.Rsi2Config(universe=("NVDA", "AAPL"), excluded=()))
    monkeypatch.setattr("webull_web.paper_service.now_et",
                        lambda: (f"{today}T17:00:00-04:00", today))
    monkeypatch.setattr("webull_web.paper_service.last_prices",
                        lambda syms: {"NVDA": 200.0, "AAPL": 100.0})
    monkeypatch.setattr("webull_web.journal_ingest.sync_paper", lambda: None)
    if stub_settle:
        # These tests exercise decide()/placement, not the settle-at-open step (that's
        # test_paper_settle_service.py + the integration test below) — keep them isolated.
        monkeypatch.setattr(svc.paper_settle_service, "settle_book",
                            lambda *a, **k: {"settled": [], "rejected": [], "resting": [],
                                             "stale": [], "errors": []})


def test_run_places_exit_and_entry_and_records(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch,
          positions={"NVDA": {"symbol": "NVDA", "quantity": 31, "avg_cost": 192.0}},
          lots=[{"symbol": "NVDA", "shares": 31, "entry_price": 192.0,
                 "entry_date": "2026-06-29", "paper_order_id": "seed"}])
    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),   # rising -> RSI 100 -> exit
            "AAPL": _raw([8, 9, 10, 11, 12], "2026-07-08")}    # falling -> RSI 0 -> entry
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])

    out = svc.run()
    assert out["result"] == "ok" and out["placed"] == 2 and out["exits"] == 1 and out["entries"] == 1
    assert out["summary"].startswith("RSI2: queued")

    from webull_web import rsi2_store, paper_service
    from webull_api import run_log, action_log
    state = rsi2_store.load()
    owned = {l["symbol"] for l in state["owned_lots"]}
    assert owned == {"NVDA"}  # nothing settles same-run: queued orders never touch owned_lots
    pend = {(p["symbol"], p["side"]) for p in state["pending_orders"]}
    assert pend == {("NVDA", "SELL"), ("AAPL", "BUY")}
    acct = paper_service.load_account()
    assert all(o.status == "pending" and o.fill_policy == "next_open" for o in acct.open_orders)
    assert run_log.last_for("rsi2")["result"] == "ok"
    whys = [a["why"] for a in action_log.load()]
    assert any("exit" in w for w in whys) and any("entry" in w for w in whys)


def test_run_no_op_when_no_fresh_bar(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, today="2026-07-09")  # clock says the 9th…
    bars = {"NVDA": _raw([12, 11, 10], "2026-07-08"),   # …but bars are dated the 8th (holiday)
            "AAPL": _raw([8, 9, 10], "2026-07-08")}
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])
    out = svc.run()
    assert out["result"] == "no_op" and out["placed"] == 0
    from webull_web import rsi2_store
    assert rsi2_store.load()["owned_lots"] == []


def test_run_no_fresh_bar_but_stale_queue_reports_error(tmp_path, monkeypatch):
    """A holiday/no-bar run must not hide a queued order that never settled — settle
    visibility folds into the no-fresh-bar branch too, not just the post-decide summary."""
    from types import SimpleNamespace
    _seed(tmp_path, monkeypatch, today="2026-07-09", stub_settle=False)  # clock says the 9th…
    bars = {"NVDA": _raw([12, 11, 10], "2026-07-08"),   # …but bars are dated the 8th (holiday)
            "AAPL": _raw([8, 9, 10], "2026-07-08")}
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])
    stale_order = SimpleNamespace(paper_order_id="stale1", symbol="AAPL", placed_et_date="2026-07-07")
    monkeypatch.setattr(svc.paper_settle_service, "settle_book",
                        lambda *a, **k: {"settled": [], "rejected": [], "resting": [stale_order],
                                         "stale": [stale_order], "errors": []})
    out = svc.run()
    assert out["result"] == "error"
    assert "stale queued order" in out["summary"]


def test_run_degrades_when_a_symbol_raises(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch,
          positions={"NVDA": {"symbol": "NVDA", "quantity": 31, "avg_cost": 192.0}},
          lots=[{"symbol": "NVDA", "shares": 31, "entry_price": 192.0,
                 "entry_date": "2026-06-29", "paper_order_id": "seed"}])

    def flaky(sym, *a, **k):
        if sym == "NVDA":
            raise RuntimeError("bars down")
        return _raw([8, 9, 10, 11, 12], "2026-07-08")  # AAPL falling -> entry
    monkeypatch.setattr("webull_api.market_data.get_bars", flaky)

    out = svc.run()
    from webull_web import rsi2_store
    state = rsi2_store.load()
    owned = {l["symbol"] for l in state["owned_lots"]}
    assert owned == {"NVDA"}  # un-exited (no data), still held; AAPL not yet a lot — only queued
    pending_syms = {p["symbol"] for p in state["pending_orders"]}
    assert pending_syms == {"AAPL"}
    assert out["entries"] == 1 and any("NVDA" in e for e in out["errors"])


def test_same_day_guard_no_ops(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    (tmp_path / "activity").mkdir(exist_ok=True)
    (tmp_path / "activity" / "runs.jsonl").write_text(
        json.dumps({"key": "rsi2", "ts": "2026-07-08T17:00:00-04:00", "result": "ok"}) + "\n",
        encoding="utf-8")
    monkeypatch.setattr("webull_api.market_data.get_bars",
                        lambda s, *a, **k: (_ for _ in ()).throw(AssertionError("must not fetch")))
    out = svc.run()
    assert out["result"] == "no_op" and "already ran today" in out["summary"]


def test_run_error_when_all_fetches_fail(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    monkeypatch.setattr("webull_api.market_data.get_bars",
                        lambda s, *a, **k: (_ for _ in ()).throw(RuntimeError("network down")))
    out = svc.run()
    assert out["result"] == "error" and out["placed"] == 0 and out["errors"]  # outage, not a healthy no-op
    from webull_api import run_log
    assert run_log.last_for("rsi2")["result"] == "error"


def test_owned_symbol_dropped_from_universe_still_exits(tmp_path, monkeypatch):
    # A held lot whose symbol left the universe must still be managed to its RSI>70 exit:
    # the service fetches universe ∪ owned, and the engine exits over owned_lots.
    _seed(tmp_path, monkeypatch,
          positions={"GRAB": {"symbol": "GRAB", "quantity": 100, "avg_cost": 4.0}},
          lots=[{"symbol": "GRAB", "shares": 100, "entry_price": 4.0,
                 "entry_date": "2026-06-29", "paper_order_id": "seed"}])
    monkeypatch.setattr("webull_web.paper_service.last_prices",
                        lambda syms: {"NVDA": 200.0, "AAPL": 100.0, "GRAB": 5.0})
    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),    # rising -> RSI 100, unowned -> no-op
            "AAPL": _raw([12, 11, 10, 9, 8], "2026-07-08"),    # rising -> RSI 100, no entry (<10 only)
            "GRAB": _raw([5.0, 4.5, 4.0, 3.5, 3.0], "2026-07-08")}  # rising -> RSI 100 -> EXIT
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])

    out = svc.run()
    assert out["exits"] == 1 and out["entries"] == 0
    from webull_web import rsi2_store
    state = rsi2_store.load()
    # queued SELL hasn't settled/adopted yet this run — the lot is untouched until it does
    assert [l["symbol"] for l in state["owned_lots"]] == ["GRAB"]
    assert [(p["symbol"], p["side"]) for p in state["pending_orders"]] == [("GRAB", "SELL")]


def test_run_settles_yesterdays_queue_then_adopts(tmp_path, monkeypatch):
    """Yesterday's queued BUY settles at today's open and becomes an owned lot BEFORE deciding —
    exercised on the MAIN path (settle -> adopt -> reconcile -> decide), not the no-fresh-bar
    early return. A single ~30-bar daily series serves both the settle-window fetch and the
    RSI-window fetch (both request count=30 now, so they're no longer distinguishable and must
    share one realistic series): the newest bar is dated today with open=205.0 (the price the
    queued BUY settles at); closes alternate, which converges RSI(2) to a steady 33.3/66.7
    oscillation (see the fixed-point derivation this mirrors) — safely inside the neutral
    (10, 70) band, so decide() proposes no new entries/exits and the test stays focused on
    settle->adopt."""
    from datetime import date, timedelta
    _seed(tmp_path, monkeypatch, today="2026-07-08", cash=79000.0, stub_settle=False)

    queued = {
        "paper_order_id": "pend1", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
        "quantity": 10.0, "limit_price": None, "time_in_force": "DAY", "status": "pending",
        "created_at": "2026-07-07T17:00:00-04:00", "placed_et_date": "2026-07-07",
        "filled_at": None, "fill_price": None, "thesis": None, "strategy_id": None,
        "trial_id": None, "fill_policy": "next_open", "ref_price": 200.0, "fill_session": None,
    }
    (tmp_path / "paper" / "default.json").write_text(json.dumps({
        "account_id": "default", "starting_cash": 100000.0, "cash": 79000.0,
        "positions": {}, "open_orders": [queued], "history": [],
        "realized_pnl": 0.0, "created_at": "2026-07-08", "updated_at": "2026-07-08"}),
        encoding="utf-8")
    (tmp_path / "activity").mkdir(exist_ok=True)
    (tmp_path / "activity" / "rsi2_state.json").write_text(json.dumps({
        "schema_version": 1, "owned_lots": [],
        "pending_orders": [{"paper_order_id": "pend1", "symbol": "AAPL", "side": "BUY",
                            "placed_date": "2026-07-07"}],
        "reconciliation_log": [], "updated_at": None}), encoding="utf-8")

    today_d = date(2026, 7, 8)
    dates = [(today_d - timedelta(days=n)).isoformat() for n in range(29, -1, -1)]  # oldest..today
    closes = [200.0 if i % 2 == 0 else 201.0 for i in range(30)]  # alternating -> neutral RSI(2)
    rows_oldest_first = []
    for i, (d, c) in enumerate(zip(dates, closes)):
        is_today = i == len(dates) - 1
        rows_oldest_first.append({
            "time": d, "open": "205.0" if is_today else f"{c - 0.5:.1f}",
            "high": f"{c + 1:.1f}", "low": f"{c - 1:.1f}", "close": f"{c:.1f}", "volume": "1",
        })
    bars = list(reversed(rows_oldest_first))  # newest-first, like the SDK
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars)

    out = svc.run(force=True)
    assert out["summary"].startswith("RSI2: queued")  # main path, not the no-fresh-bar branch
    assert "settled 1" in out["summary"]

    from webull_web import rsi2_store, paper_service
    acct = paper_service.load_account()
    assert acct.positions["AAPL"].quantity == 10.0
    assert acct.positions["AAPL"].avg_cost == 205.0  # filled at today's actual open, not ref_price

    state = rsi2_store.load()
    assert state["pending_orders"] == []
    assert len(state["owned_lots"]) == 1
    lot = state["owned_lots"][0]
    assert lot["symbol"] == "AAPL" and lot["entry_price"] == 205.0
    assert lot["entry_date"] == "2026-07-08" and lot["paper_order_id"] == "pend1"


def test_force_rerun_does_not_double_queue(tmp_path, monkeypatch):
    """decide() is queue-blind: a queued next-open order lives in neither owned_lots nor
    positions. A same-evening --force rerun with the same persistent signal must not re-queue
    the symbol a second time (2x sizing) — the in-flight filter has to catch it."""
    _seed(tmp_path, monkeypatch)
    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),   # rising -> RSI 100, unowned -> no-op
            "AAPL": _raw([8, 9, 10, 11, 12], "2026-07-08")}    # falling -> RSI 0 -> entry
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])

    out1 = svc.run()
    assert out1["entries"] == 1 and out1["placed"] == 1

    out2 = svc.run(force=True)
    assert out2["entries"] == 0 and out2["placed"] == 0
    assert "0 entr" in out2["summary"]

    from webull_web import rsi2_store, paper_service
    acct = paper_service.load_account()
    aapl_orders = [o for o in acct.open_orders if o.symbol == "AAPL"]
    assert len(aapl_orders) == 1  # still just the one queued entry, not two

    state = rsi2_store.load()
    aapl_pending = [p for p in state["pending_orders"] if p["symbol"] == "AAPL"]
    assert len(aapl_pending) == 1


def test_persistent_signal_with_unsettled_queue_does_not_double_queue(tmp_path, monkeypatch):
    """A settle failure (order still resting/stale) must not let a persistent RSI<10 signal
    re-queue the same symbol — decide() only sees owned_lots/positions, never the open
    next-open order sitting unsettled in the account."""
    from types import SimpleNamespace
    _seed(tmp_path, monkeypatch, today="2026-07-08", cash=79000.0, stub_settle=False)

    queued = {
        "paper_order_id": "pend1", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
        "quantity": 10.0, "limit_price": None, "time_in_force": "DAY", "status": "pending",
        "created_at": "2026-07-07T17:00:00-04:00", "placed_et_date": "2026-07-07",
        "filled_at": None, "fill_price": None, "thesis": None, "strategy_id": None,
        "trial_id": None, "fill_policy": "next_open", "ref_price": 100.0, "fill_session": None,
    }
    (tmp_path / "paper" / "default.json").write_text(json.dumps({
        "account_id": "default", "starting_cash": 100000.0, "cash": 79000.0,
        "positions": {}, "open_orders": [queued], "history": [],
        "realized_pnl": 0.0, "created_at": "2026-07-08", "updated_at": "2026-07-08"}),
        encoding="utf-8")
    (tmp_path / "activity").mkdir(exist_ok=True)
    (tmp_path / "activity" / "rsi2_state.json").write_text(json.dumps({
        "schema_version": 1, "owned_lots": [],
        "pending_orders": [{"paper_order_id": "pend1", "symbol": "AAPL", "side": "BUY",
                            "placed_date": "2026-07-07"}],
        "reconciliation_log": [], "updated_at": None}), encoding="utf-8")

    # Settle failed: the order is still resting from yesterday -> reported stale.
    stale_order = SimpleNamespace(paper_order_id="pend1", symbol="AAPL", placed_et_date="2026-07-07")
    monkeypatch.setattr(svc.paper_settle_service, "settle_book",
                        lambda *a, **k: {"settled": [], "rejected": [], "resting": [stale_order],
                                         "stale": [stale_order], "errors": []})

    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),    # rising -> RSI 100, unowned -> no-op
            "AAPL": _raw([8, 9, 10, 11, 12], "2026-07-08")}     # falling -> RSI 0 -> would-be 2nd entry
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])

    out = svc.run()
    assert out["entries"] == 0
    assert "stale queued order" in out["summary"]

    from webull_web import rsi2_store, paper_service
    acct = paper_service.load_account()
    aapl_orders = [o for o in acct.open_orders if o.symbol == "AAPL"]
    assert len(aapl_orders) == 1  # still just the original queued order, no duplicate

    state = rsi2_store.load()
    aapl_pending = [p for p in state["pending_orders"] if p["symbol"] == "AAPL"]
    assert len(aapl_pending) == 1


# --- earnings observation (spec 2026-08-03): observational only, never a gate ---

def _entry_only_bars(monkeypatch):
    # AAPL falling -> RSI 0 -> BUY; NVDA rising + not owned -> no action
    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),
            "AAPL": _raw([8, 9, 10, 11, 12], "2026-07-08")}
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])


def test_buy_row_logs_next_earnings_date(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    _entry_only_bars(monkeypatch)
    monkeypatch.setattr(svc, "_next_earnings_checked", lambda s: ("2026-07-15", True))
    out = svc.run()
    assert out["result"] == "ok" and out["entries"] == 1
    from webull_api import action_log
    buy = next(a for a in action_log.load() if a["side"] == "BUY")
    assert buy["next_earnings"] == "2026-07-15"
    assert buy["earnings_checked"] is True
    assert "next earnings 2026-07-15" in buy["why"]


def test_buy_row_logs_verified_clear(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    _entry_only_bars(monkeypatch)
    monkeypatch.setattr(svc, "_next_earnings_checked", lambda s: (None, True))
    svc.run()
    from webull_api import action_log
    buy = next(a for a in action_log.load() if a["side"] == "BUY")
    assert buy["next_earnings"] is None
    assert buy["earnings_checked"] is True
    assert "no earnings" in buy["why"]


def test_earnings_source_failure_never_blocks_entry(tmp_path, monkeypatch):
    """The never-blocks guarantee: the REAL wrapper catches a raising news source,
    the entry still queues, the run is still ok, the row says unchecked."""
    _seed(tmp_path, monkeypatch)
    _entry_only_bars(monkeypatch)

    def _boom(*a, **k):
        raise RuntimeError("finnhub down")
    monkeypatch.setattr("webull_web.news.get_next_earnings_checked", _boom)
    out = svc.run()
    assert out["result"] == "ok" and out["entries"] == 1
    from webull_api import action_log
    buy = next(a for a in action_log.load() if a["side"] == "BUY")
    assert buy["next_earnings"] is None
    assert buy["earnings_checked"] is False
    assert "earnings unchecked" in buy["why"]


def test_sell_rows_carry_no_earnings_fields(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch,
          positions={"NVDA": {"symbol": "NVDA", "quantity": 31, "avg_cost": 192.0}},
          lots=[{"symbol": "NVDA", "shares": 31, "entry_price": 192.0,
                 "entry_date": "2026-06-29", "paper_order_id": "seed"}])
    bars = {"NVDA": _raw([12, 11, 10, 9, 8], "2026-07-08"),   # rising -> RSI 100 -> exit
            "AAPL": _raw([8, 9, 10, 11, 12], "2026-07-08")}    # falling -> RSI 0 -> entry
    monkeypatch.setattr("webull_api.market_data.get_bars", lambda s, *a, **k: bars[s])
    monkeypatch.setattr(svc, "_next_earnings_checked", lambda s: ("2026-07-15", True))
    out = svc.run()
    assert out["exits"] == 1 and out["entries"] == 1
    from webull_api import action_log
    sell = next(a for a in action_log.load() if a["side"] == "SELL")
    assert "next_earnings" not in sell and "earnings_checked" not in sell
