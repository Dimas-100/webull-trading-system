"""The real RSI2 runner. It QUEUES executable decisions and never places — the autopilot's
DECISIONS stage is the only thing that can turn a row into an order."""
import inspect
import re

import pytest

from webull_web import rsi2_real_service as svc

ACCOUNTS = [
    {"account_id": "co-crypto", "account_class": "CRYPTO", "account_type": "CASH"},
    {"account_id": "EQ1", "account_class": "INDIVIDUAL_CASH", "account_type": "CASH"},
]
BALANCE = {"account_currency_assets": [
    {"currency": "USD", "cash_balance": "308.41", "settled_cash": "308.41",
     "unsettled_cash": "0.00", "buying_power": "308.41"}]}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ACTIVITY_DIR", str(tmp_path))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))   # adoption reads the journal
    monkeypatch.setenv("WEBULL_DECISIONS_FILE", str(tmp_path / "q.jsonl"))
    monkeypatch.delenv("WEBULL_RSI2_REAL_ENABLED", raising=False)
    # The package loads the operator's .env on import; every env this runner reads is cleared
    # here so a value the owner sets in .env (the divisor, DOLLARS, MAX_LOTS) can never leak
    # into a test — tests that need one set it explicitly via _enabled / _floating.
    monkeypatch.delenv("WEBULL_RSI2_REAL_SLOT_DIVISOR", raising=False)
    monkeypatch.delenv("WEBULL_RSI2_REAL_DOLLARS", raising=False)
    monkeypatch.delenv("WEBULL_RSI2_REAL_MAX_LOTS", raising=False)


def _run(**kw):
    kw.setdefault("list_fn", lambda: ACCOUNTS)
    kw.setdefault("positions_fn", lambda aid: [])
    kw.setdefault("balance_fn", lambda aid: BALANCE)
    kw.setdefault("rsi_fn", lambda today, syms, errors: {})
    kw.setdefault("price_fn", lambda syms: {})
    return svc.run(force=True, **kw)


def test_disabled_by_default_does_nothing_but_log(monkeypatch):
    called = []
    out = _run(list_fn=lambda: called.append("broker") or ACCOUNTS)
    assert out["result"] == "no_op" and "disabled" in out["summary"]
    assert called == [], "a disabled runner must not touch the broker"


def test_disabled_still_writes_a_run_log_row(monkeypatch):
    from webull_api import run_log
    _run()
    assert run_log.last_for("rsi2_real") is not None, "watchdog EXPECTED_KEYS would alarm"


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes"])
def test_enabled_accepts_common_truthy_spellings(monkeypatch, val):
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", val)
    assert svc.enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "", "maybe"])
def test_enabled_rejects_everything_else(monkeypatch, val):
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", val)
    assert svc.enabled() is False


def test_account_resolution_skips_the_crypto_account():
    assert svc.real_account_id(lambda: ACCOUNTS) == "EQ1"


def test_account_resolution_returns_none_when_absent():
    assert svc.real_account_id(lambda: [ACCOUNTS[0]]) is None


def test_settled_cash_reads_the_settled_field_not_buying_power():
    assert svc.settled_cash(BALANCE) == 308.41
    partial = {"account_currency_assets": [
        {"cash_balance": "500.00", "settled_cash": "100.00", "buying_power": "500.00"}]}
    assert svc.settled_cash(partial) == 100.00


def test_settled_cash_is_none_when_the_field_is_missing():
    """Fail closed: an unreadable settled balance must not fall back to buying_power."""
    assert svc.settled_cash({"account_currency_assets": [{"buying_power": "500.00"}]}) is None
    assert svc.settled_cash({}) is None


def test_enabled_run_reconciles_the_ledger(monkeypatch):
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", "1")
    from webull_web import rsi2_real_store as store
    s = store.record_entry(store.load(), symbol="GOOG", shares=5, entry_price=1.0,
                           entry_date="2026-08-17", decision_id="d")
    store.save(s, "2026-08-17T00:00:00")
    out = _run()
    assert out["result"] in ("ok", "no_op")
    assert store.load()["owned_lots"] == []      # broker holds nothing -> dropped


def test_missing_real_account_is_an_error_not_a_crash(monkeypatch):
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", "1")
    out = _run(list_fn=lambda: [ACCOUNTS[0]])
    assert out["result"] == "error" and "no INDIVIDUAL_CASH" in out["summary"]


def test_a_filled_pending_becomes_an_owned_lot(monkeypatch):
    """The loop that makes the sleeve work: queue -> autopilot places -> broker holds it ->
    this run adopts it. Without adoption owned_lots stays empty and entries repeat nightly."""
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", "1")
    from webull_web import rsi2_real_store as store
    s = store.record_pending(store.load(), decision_id="d1", symbol="AAPL", shares=1,
                             queued_date="2026-08-17")
    store.save(s, "2026-08-17T00:00:00")
    _run(positions_fn=lambda aid: [{"symbol": "AAPL", "quantity": "1", "cost_price": "303.00"}])
    after = store.load()
    assert [l["symbol"] for l in after["owned_lots"]] == ["AAPL"]
    assert after["pending_orders"] == []


from webull_api import decisions_exec


def _enabled(monkeypatch, **env):
    monkeypatch.setenv("WEBULL_RSI2_REAL_ENABLED", "1")
    for k, v in env.items():
        monkeypatch.setenv(k, v)


@pytest.mark.parametrize("today,expected", [
    ("2026-08-14", "2026-08-17"),   # Fri -> Mon
    ("2026-08-15", "2026-08-17"),   # Sat -> Mon
    ("2026-08-16", "2026-08-17"),   # Sun -> Mon
    ("2026-08-17", "2026-08-18"),   # Mon -> Tue
])
def test_next_trading_day_skips_the_weekend(today, expected):
    """C1: a row stamped `expires == today` is expired before the 120-min BUY cooling ever
    lets the autopilot place it. The next trading weekday is the earliest usable expiry."""
    assert svc._next_trading_day(today) == expected


def test_entry_is_queued_never_placed(monkeypatch):
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    out = _run(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert len(rows) == 1
    assert rows[0]["symbol"] == "AAPL" and rows[0]["side"] == "BUY"
    assert rows[0]["qty"] == "1"
    # C2: MARKET rows are unpriceable at the gate ('cannot price order to enforce the cap')
    # and would be denied forever. C1: the row must outlive tonight's cooling window.
    assert rows[0]["order_type"] == "LIMIT" and rows[0]["limit_price"] == "303.00"
    assert rows[0]["trigger"] == {"kind": "immediate"}
    assert rows[0]["status"] == "queued"
    from webull_web import paper_service
    _, today = paper_service.now_et()
    assert rows[0]["expires"] == svc._next_trading_day(today) > today
    assert "303.00" in rows[0]["note"]
    assert out["entries"] == ["AAPL"]


def test_sizing_uses_the_padded_limit_not_the_last_close(monkeypatch):
    """C2: the queued price is the 1%-padded limit, so sizing must spend at THAT price — a cash
    account rejects an over-budget fill. $302 settled at a $300 close buys 0 shares (limit 303)."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    bal = {"account_currency_assets": [{"settled_cash": "302.00"}]}
    out = _run(balance_fn=lambda aid: bal,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert rows == [], "MARKET-at-300 would wrongly buy 1 share the cash cannot fund"
    assert out["unaffordable"] == [{"symbol": "AAPL", "price": 303.0, "rsi2": 5.0}]


def test_entry_sized_from_settled_cash_not_buying_power(monkeypatch):
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    bal = {"account_currency_assets": [
        {"cash_balance": "900.00", "settled_cash": "310.00", "buying_power": "900.00"}]}
    _run(balance_fn=lambda aid: bal,
         rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
         price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert rows[0]["qty"] == "1", "sizing must use settled_cash (310), not buying_power (900)"


def test_unreadable_settled_cash_queues_nothing(monkeypatch):
    _enabled(monkeypatch)
    out = _run(balance_fn=lambda aid: {"account_currency_assets": [{"buying_power": "900"}]},
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert rows == [] and "settled cash" in out["summary"]


def test_unaffordable_signal_is_recorded_not_silently_dropped(monkeypatch):
    _enabled(monkeypatch)
    bal = {"account_currency_assets": [{"settled_cash": "100.00"}]}
    out = _run(balance_fn=lambda aid: bal,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert rows == []
    assert out["unaffordable"] == [{"symbol": "AAPL", "price": 303.0, "rsi2": 5.0}]  # padded limit
    assert "unaffordable" in out["summary"]


def test_no_signal_queues_nothing(monkeypatch):
    _enabled(monkeypatch)
    out = _run(rsi_fn=lambda today, syms, errors: {"AAPL": 55.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    rows, _ = decisions_exec.load()
    assert rows == [] and out["entries"] == []


def test_a_queued_entry_is_a_pending_not_a_lot(monkeypatch):
    """The invariant that keeps attribution honest: queueing creates an INTENT. Only a
    broker-verified fill (next run's adopt) may create a lot."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    from webull_web import rsi2_real_store as store
    _run(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
         price_fn=lambda syms: {"AAPL": 300.0})
    s = store.load()
    assert s["owned_lots"] == []
    assert [p["symbol"] for p in s["pending_orders"]] == ["AAPL"]


def test_the_slot_is_not_double_spent_while_a_pending_is_outstanding(monkeypatch):
    """Two runs before the fill lands must not queue two entries for one slot."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    sig = dict(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0, "JNJ": 4.0},
               price_fn=lambda syms: {"AAPL": 300.0, "JNJ": 150.0})
    _run(**sig)
    _run(**sig)
    rows, _ = decisions_exec.load()
    assert len([r for r in rows if r["side"] == "BUY"]) == 1


def _own(symbol="AAPL", shares=1):
    from webull_web import rsi2_real_store as store
    s = store.record_entry(store.load(), symbol=symbol, shares=shares, entry_price=303.0,
                           entry_date="2026-08-17", decision_id="d")
    store.save(s, "2026-08-17T00:00:00")


def test_a_held_lot_gets_a_standing_rsi2_above_exit(monkeypatch):
    _enabled(monkeypatch)
    _own()
    out = _run(positions_fn=lambda aid: [{"symbol": "AAPL", "quantity": "1"}])
    rows, _ = decisions_exec.load()
    sells = [r for r in rows if r["side"] == "SELL"]
    assert len(sells) == 1
    # M1: the band travels with the row so the executor cannot disagree about the number.
    assert sells[0]["trigger"] == {"kind": "rsi2_above", "threshold": 70.0}
    assert "70" in sells[0]["note"]
    assert sells[0]["qty"] == "ALL" and sells[0]["symbol"] == "AAPL"
    assert out["exits_queued"] == ["AAPL"]
    from webull_web import rsi2_real_store as store
    assert store.load()["exit_rows"] == {"AAPL": sells[0]["id"]}


def test_exit_queuing_is_idempotent_across_runs(monkeypatch):
    _enabled(monkeypatch)
    _own()
    pos = lambda aid: [{"symbol": "AAPL", "quantity": "1"}]
    _run(positions_fn=pos)
    out = _run(positions_fn=pos)
    rows, _ = decisions_exec.load()
    assert len([r for r in rows if r["side"] == "SELL"]) == 1, "must not stack duplicate exits"
    assert out["exits_queued"] == []


def test_no_exit_row_for_a_lot_that_is_gone(monkeypatch):
    _enabled(monkeypatch)
    _own()
    out = _run(positions_fn=lambda aid: [])     # reconcile drops the lot first
    rows, _ = decisions_exec.load()
    assert [r for r in rows if r["side"] == "SELL"] == []


def test_an_orphaned_exit_row_is_cancelled_when_its_lot_leaves(monkeypatch):
    """C3: a standing `qty: ALL` SELL outliving its lot would liquidate ANY later position in
    that symbol the moment RSI(2) crosses the band."""
    _enabled(monkeypatch)
    _own()
    from webull_web import rsi2_real_store as store
    _run(positions_fn=lambda aid: [{"symbol": "AAPL", "quantity": "1"}])
    mine = store.load()["exit_rows"]["AAPL"]
    out = _run(positions_fn=lambda aid: [])     # the position is gone -> lot dropped
    rows, _ = decisions_exec.load()
    assert {r["id"]: r["status"] for r in rows}[mine] == "cancelled"
    assert store.load()["exit_rows"] == {}      # map entry retired with the row
    assert any("AAPL" in n for n in out["reconciled"])


def test_a_foreign_rsi2_above_row_is_never_cancelled(monkeypatch):
    """The queue also carries the owner's OWN hand-queued rsi2_above SELL protecting a share this
    ledger does not own. Cancellation is by recorded id only — never by symbol/kind pattern."""
    _enabled(monkeypatch)
    _own()
    from webull_web import rsi2_real_store as store
    _run(positions_fn=lambda aid: [{"symbol": "AAPL", "quantity": "1"}])
    mine = store.load()["exit_rows"]["AAPL"]
    foreign = decisions_exec.append({
        "asset": "EQUITY", "symbol": "AAPL", "side": "SELL", "qty": "ALL",
        "order_type": "MARKET", "trigger": {"kind": "rsi2_above", "threshold": 70},
        "expires": "2026-12-31", "note": "owner's own hand-queued exit"})
    _run(positions_fn=lambda aid: [])
    status = {r["id"]: r["status"] for r in decisions_exec.load()[0]}
    assert status[mine] == "cancelled"
    assert status[foreign["id"]] == "queued", "the owner's protection must be untouchable"


def _rows_for(key="rsi2_real"):
    from webull_api import run_log
    return [r for r in run_log.load() if r.get("key") == key]


def test_a_market_data_entitlement_error_still_writes_exactly_one_run_log_row(monkeypatch):
    """I3: an escaping read skips the run-log row and the nightly watchdog pages on the gap.
    The 'always exactly one row' contract outranks re-raising the entitlement error."""
    _enabled(monkeypatch)
    from webull_api.market_data import MarketDataNotEntitledError

    def boom(syms):
        raise MarketDataNotEntitledError("Insufficient permission: Nasdaq Basic Non-Display")

    out = _run(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0}, price_fn=boom)
    assert out["result"] == "error"
    assert "MarketDataNotEntitled" in out["summary"], "the note must surface the entitlement"
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


@pytest.mark.parametrize("kw,fn", [
    ("list_fn", lambda: (_ for _ in ()).throw(RuntimeError("broker 429"))),
    ("rsi_fn", lambda today, syms, errors: (_ for _ in ()).throw(RuntimeError("bars 500"))),
])
def test_an_escaping_read_becomes_an_error_report_not_a_crash(monkeypatch, kw, fn):
    _enabled(monkeypatch)
    out = _run(**{kw: fn})
    assert out["result"] == "error" and "RuntimeError" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_a_dead_decision_frees_the_slot_on_the_next_run(monkeypatch):
    """I1: an expired/failed/gate-denied decision must not hold the lot's slot for the 5-day TTL."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    sig = dict(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    _run(**sig)
    first = decisions_exec.load()[0][0]
    decisions_exec.mark(first["id"], "expired", "cooling window closed")
    _run(**sig)
    buys = [r for r in decisions_exec.load()[0] if r["side"] == "BUY"]
    assert len(buys) == 2, "the slot must reopen once its decision is known dead"


def test_a_live_pending_still_holds_its_slot(monkeypatch):
    """The other direction: a decision still queued/placing keeps the slot (no double-spend)."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    sig = dict(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    _run(**sig)
    decisions_exec.mark(decisions_exec.load()[0][0]["id"], "placing", "in flight")
    _run(**sig)
    assert len([r for r in decisions_exec.load()[0] if r["side"] == "BUY"]) == 1


# ---- source guards (I4): this surface must stay structurally unable to place -----------

def _no_order_path(module):
    src = inspect.getsource(module)
    for forbidden in ("trading.place", "confirm=True", "import trading", "place_order",
                      "webull_api.trading", "webull_api.safety"):
        assert forbidden not in src, f"{module.__name__} must not contain {forbidden!r}"
    for line in src.splitlines():
        s = line.strip()
        if s.startswith("import ") or s.startswith("from "):
            assert "trading" not in s and "safety" not in s, f"forbidden import: {s!r}"
    return src


def test_the_real_runner_has_no_submit_path():
    src = _no_order_path(svc)
    # The ONLY queue writes allowed are append + mark(..., "cancelled", ...) on ids this runner
    # recorded itself. Anything else (a pattern-matched cancel, a status rewrite) is out of bounds.
    assert set(re.findall(r"decisions_exec\.(\w+)", src)) <= {"append", "mark", "load"}
    assert re.findall(r'decisions_exec\.mark\(\s*(\w+)', src) == ["decision_id"]


def test_the_real_ledger_has_no_submit_path():
    from webull_web import rsi2_real_store
    src = _no_order_path(rsi2_real_store)
    assert "decisions_exec" not in src, "the ledger is pure file I/O — it never touches the queue"


def test_the_attribution_module_has_no_submit_path():
    from webull_web import rsi2_real_attribution
    src = _no_order_path(rsi2_real_attribution)
    assert "decisions_exec" not in src, "attribution reads the journal and appends actions — never the queue"


# ---- config parsing (M7) ---------------------------------------------------------------

def test_an_unparseable_dollar_budget_queues_nothing(monkeypatch):
    """M7: falling back to None here means 'spend all settled cash' — a typo in the env must
    never silently widen the per-signal budget."""
    _enabled(monkeypatch, WEBULL_RSI2_REAL_DOLLARS="three hundred")
    out = _run(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "error" and "WEBULL_RSI2_REAL_DOLLARS" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_an_unset_dollar_budget_spends_settled_cash(monkeypatch):
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1")
    monkeypatch.delenv("WEBULL_RSI2_REAL_DOLLARS", raising=False)
    out = _run(rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["entries"] == ["AAPL"] and decisions_exec.load()[0][0]["qty"] == "1"


# ---- equity-tracking budget (spec 2026-09-23): net liq / divisor, clipped under the cap ---------

BALANCE_3K = {
    "total_net_liquidation_value": "3037.39",
    "account_currency_assets": [
        {"currency": "USD", "net_liquidation_value": "3037.39", "cash_balance": "2446.26",
         "settled_cash": "2446.26", "unsettled_cash": "0.00", "buying_power": "2446.26"}]}


def _floating(monkeypatch, **env):
    """Divisor on, cap pinned (nothing in the module loads .env; the parser would default to $40).
    `env` overrides the defaults (a test may pass its own divisor or cap)."""
    monkeypatch.delenv("WEBULL_RSI2_REAL_DOLLARS", raising=False)
    settings = {"WEBULL_RSI2_REAL_SLOT_DIVISOR": "6", "WEBULL_AUTOPILOT_MAX_NOTIONAL": "525"}
    settings.update(env)
    _enabled(monkeypatch, **settings)


def test_net_liq_reads_the_total_then_the_currency_row_never_buying_power():
    assert svc.net_liq(BALANCE_3K) == 3037.39
    assert svc.net_liq({"account_currency_assets": [
        {"net_liquidation_value": "12.50", "buying_power": "900"}]}) == 12.5
    assert svc.net_liq({"account_currency_assets": [{"buying_power": "900"}]}) is None
    assert svc.net_liq({"total_net_liquidation_value": "n/a"}) is None
    assert svc.net_liq({}) is None


def test_divisor_sizes_a_sixth_of_net_liq_never_settled_cash(monkeypatch):
    """$3,037.39 / 6 = $506.23 buys one $300 name (limit 303) and skips a $520 name (limit 525.20)
    as unaffordable. Settled cash is $2,446 — the old path would have bought four MSFT."""
    _floating(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="2")
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0, "MSFT": 4.0},
               price_fn=lambda syms: {"AAPL": 300.0, "MSFT": 520.0})
    rows, _ = decisions_exec.load()
    assert [(r["symbol"], r["qty"]) for r in rows] == [("AAPL", "1")]
    assert [u["symbol"] for u in out["unaffordable"]] == ["MSFT"]
    assert out["budget"] == pytest.approx(3037.39 / 6) and out["budget_basis"] == "divisor"
    assert out["net_liq"] == 3037.39
    assert "budget $506.23 (net liq $3,037.39 ÷ 6)" in out["summary"]


def test_divisor_never_queues_a_row_the_cap_would_deny(monkeypatch):
    """The seam that keeps the gate's cap layer from ever denying a queued row: $6,000 / 6 = $1,000
    but the cap is $525, so every queued row's qty x limit_price <= 525 (gate._notional's formula)."""
    bal = {"total_net_liquidation_value": "6000.00",
           "account_currency_assets": [{"settled_cash": "6000.00", "net_liquidation_value": "6000.00"}]}
    _floating(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="3")
    out = _run(balance_fn=lambda aid: bal,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0, "WMT": 3.0, "JNJ": 6.0},
               price_fn=lambda syms: {"AAPL": 300.0, "WMT": 110.0, "JNJ": 269.0})
    rows, _ = decisions_exec.load()
    assert [r["symbol"] for r in rows] == ["WMT", "AAPL", "JNJ"]          # most oversold first
    for r in rows:
        assert float(r["qty"]) * float(r["limit_price"]) <= 525.0
    assert rows[0]["qty"] == "4"                                           # floor(525 / 111.10)
    assert out["budget"] == 525.0 and out["budget_basis"] == "cap"
    assert ("budget $525.00 (cap binds: net liq $6,000.00 ÷ 6 = $1,000.00 — raise "
            "WEBULL_AUTOPILOT_MAX_NOTIONAL)") in out["summary"]


def test_dollars_left_beside_the_divisor_still_binds_and_the_note_says_so(monkeypatch):
    _floating(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1", WEBULL_RSI2_REAL_DOLLARS="500")
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["budget"] == 500.0 and out["budget_basis"] == "dollars"
    assert ("budget $500.00 (WEBULL_RSI2_REAL_DOLLARS binds: net liq $3,037.39 ÷ 6 = $506.23 — "
            "delete it to float)") in out["summary"]


def test_an_unparseable_divisor_queues_nothing(monkeypatch):
    _floating(monkeypatch, WEBULL_RSI2_REAL_SLOT_DIVISOR="six")
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "error" and "WEBULL_RSI2_REAL_SLOT_DIVISOR" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_a_zero_divisor_queues_nothing(monkeypatch):
    _floating(monkeypatch, WEBULL_RSI2_REAL_SLOT_DIVISOR="0")
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "error" and "WEBULL_RSI2_REAL_SLOT_DIVISOR must be > 0" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_divisor_with_no_net_liq_queues_nothing_never_settled_cash(monkeypatch):
    """BALANCE has settled cash 308.41 and no net liq field: with the divisor set that is an
    error, not a fallback — a $308 budget would be the very widening the spec forbids."""
    _floating(monkeypatch)
    out = _run(balance_fn=lambda aid: BALANCE,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "error" and "net liq unreadable" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_divisor_with_a_zero_net_liq_queues_nothing(monkeypatch):
    """The 2026-09-23 wrong-account read: net liq 0.00 beside a buying power of 2,446.26."""
    bal = {"total_net_liquidation_value": "0.00",
           "account_currency_assets": [{"settled_cash": "2446.26", "net_liquidation_value": "0.00",
                                        "buying_power": "2446.26"}]}
    _floating(monkeypatch)
    out = _run(balance_fn=lambda aid: bal,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "error" and "net liq unreadable" in out["summary"]
    assert decisions_exec.load()[0] == []
    assert len(_rows_for()) == 1


def test_unset_divisor_leaves_sizing_and_the_summary_unchanged(monkeypatch):
    """The regression lock: no divisor -> DOLLARS governs and the summary has no budget clause."""
    monkeypatch.delenv("WEBULL_RSI2_REAL_SLOT_DIVISOR", raising=False)
    _enabled(monkeypatch, WEBULL_RSI2_REAL_MAX_LOTS="1", WEBULL_RSI2_REAL_DOLLARS="420")
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert decisions_exec.load()[0][0]["qty"] == "1"
    assert "budget" not in out["summary"]
    assert out["budget"] == 420.0 and out["budget_basis"] == "unset" and out["net_liq"] is None


def test_cap_comes_from_the_autopilot_config_parser(monkeypatch):
    """One parser for the cap: a garbled WEBULL_AUTOPILOT_MAX_NOTIONAL falls back to the gate's own
    $40 default, which only sizes SMALLER (nothing affordable at $40 -> unaffordable, not an error)."""
    _floating(monkeypatch, WEBULL_AUTOPILOT_MAX_NOTIONAL="lots")
    assert svc._cap() == 40.0
    out = _run(balance_fn=lambda aid: BALANCE_3K,
               rsi_fn=lambda today, syms, errors: {"AAPL": 5.0},
               price_fn=lambda syms: {"AAPL": 300.0})
    assert out["result"] == "ok" and decisions_exec.load()[0] == []
    assert out["budget"] == 40.0 and out["budget_basis"] == "cap"


# ---- attribution (2026-09-18): an adopted lot gets one action row keyed by its journal fill

def _held_aapl(aid):
    return [{"symbol": "AAPL", "quantity": "1", "cost_price": "303.00"}]


def test_an_adopted_lot_gets_an_attribution_row_keyed_by_its_journal_fill(monkeypatch):
    """The scorecard buckets a round trip by the ENTRY fill's action row; the real runner
    wrote none, so every system trade scored as 'unattributed-equity' and the Overview called
    the fill manual. Adoption now writes exactly one row, idempotently."""
    from webull_api import action_log
    from webull_api.journal.schema import Fill
    from webull_web import journal_store, paper_service
    from webull_web import rsi2_real_store as store
    _enabled(monkeypatch)
    iso, today = paper_service.now_et()
    journal_store.append_fills([Fill(id="fill-aapl", source="real", account_id="EQ1",
                                     symbol="AAPL", side="BUY", quantity=1.0, price=303.0,
                                     filled_at_iso=f"{today}T13:31:07.000Z", order_type="LIMIT")])
    s = store.record_pending(store.load(), decision_id="d1", symbol="AAPL", shares=1,
                             queued_date=today)
    store.save(s, iso)
    out = _run(positions_fn=_held_aapl)
    rows = action_log.load()
    assert [(r["ref"], r["source"], r["sleeve"], r["symbol"], r["side"]) for r in rows] == [
        ("fill-aapl", "runner:rsi2_real", "real", "AAPL", "BUY")]
    assert out["attributed"] == ["attributed AAPL -> fill fill-aapl"]
    assert "1 attributed" in out["summary"]
    _run(positions_fn=_held_aapl)
    assert len(action_log.load()) == 1          # a re-run appends nothing


def test_attribution_failure_is_recorded_not_fatal(monkeypatch):
    from webull_web import paper_service
    from webull_web import rsi2_real_store as store
    _enabled(monkeypatch)
    iso, today = paper_service.now_et()
    store.save(store.record_pending(store.load(), decision_id="d1", symbol="AAPL", shares=1,
                                    queued_date=today), iso)

    def boom():
        raise RuntimeError("journal unreadable")

    out = _run(positions_fn=_held_aapl, fills_fn=boom)
    assert out["result"] == "ok"                              # the queue work still happened
    assert [l["symbol"] for l in store.load()["owned_lots"]] == ["AAPL"]
    assert any("attribution failed" in str(e.get("error")) for e in out["errors"])
