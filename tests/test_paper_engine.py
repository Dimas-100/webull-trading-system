from webull_api.paper.schema import PaperAccount, PaperOrder, PaperPosition, PaperError
from webull_api.safety import OrderValidationError


def test_papererror_is_ordervalidationerror_subclass():
    # so the existing 400 handler (registered for OrderValidationError) catches it via MRO
    assert issubclass(PaperError, OrderValidationError)


def test_account_serialization_round_trip():
    acct = PaperAccount(
        starting_cash=1000.0, cash=900.0, created_at="t0", updated_at="t0",
        positions={"AAPL": PaperPosition(symbol="AAPL", quantity=1.0, avg_cost=100.0)},
        open_orders=[PaperOrder(paper_order_id="o1", symbol="AAPL", side="BUY",
                                order_type="LIMIT", quantity=1.0, limit_price=95.0,
                                time_in_force="GTC", created_at="t0", placed_et_date="2026-06-16")],
    )
    again = PaperAccount.model_validate(acct.model_dump())
    assert again == acct
    assert again.positions["AAPL"].avg_cost == 100.0
    assert again.open_orders[0].status == "pending"


import pytest
from webull_api.paper import engine


def test_open_account_starts_flat():
    acct = engine.open_account(10000.0, "t0")
    assert acct.cash == 10000.0 and acct.starting_cash == 10000.0
    assert acct.positions == {} and acct.open_orders == []
    assert engine.buying_power(acct) == 10000.0


def test_place_market_buy_records_pending_order():
    acct = engine.open_account(10000.0, "t0")
    acct, order = engine.place_order(
        acct, symbol="aapl", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t0",
        today_et="2026-06-16", last_price=100.0)
    assert order.symbol == "AAPL" and order.status == "pending"
    assert len(acct.open_orders) == 1
    assert acct.cash == 10000.0  # cash only moves on fill


def test_open_limit_buy_reserves_buying_power():
    acct = engine.open_account(1000.0, "t0")
    acct, _ = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="LIMIT", quantity=5,
        limit_price=100.0, time_in_force="GTC", now_iso="t0",
        today_et="2026-06-16", last_price=100.0)
    # 5 * 100 = 500 reserved -> 500 buying power left
    assert engine.buying_power(acct) == 500.0


def test_insufficient_buying_power_raises():
    acct = engine.open_account(500.0, "t0")
    with pytest.raises(engine.PaperError):
        engine.place_order(acct, symbol="AAPL", side="BUY", order_type="MARKET",
                           quantity=10, limit_price=None, time_in_force="DAY",
                           now_iso="t0", today_et="2026-06-16", last_price=100.0)


def test_oversell_raises():
    acct = engine.open_account(10000.0, "t0")
    with pytest.raises(engine.PaperError):
        engine.place_order(acct, symbol="AAPL", side="SELL", order_type="MARKET",
                           quantity=1, limit_price=None, time_in_force="DAY",
                           now_iso="t0", today_et="2026-06-16", last_price=100.0)


def _place(acct, **kw):
    kw.setdefault("limit_price", None)
    kw.setdefault("time_in_force", "DAY")
    kw.setdefault("now_iso", "t0")
    kw.setdefault("today_et", "2026-06-16")
    kw.setdefault("last_price", 100.0)
    acct, order = engine.place_order(acct, **kw)
    return acct, order


def test_market_buy_fills_at_last():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, fills = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    assert len(fills) == 1 and fills[0].fill_price == 100.0
    assert acct.cash == 9000.0
    assert acct.positions["AAPL"].quantity == 10 and acct.positions["AAPL"].avg_cost == 100.0
    assert acct.open_orders == []


def test_limit_buy_rests_above_fills_at_or_below():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="LIMIT",
                     quantity=10, limit_price=95.0)
    acct, fills = engine.evaluate(acct, {"AAPL": 96.0}, "t1", "2026-06-16")
    assert fills == [] and len(acct.open_orders) == 1  # 96 > 95, still resting
    acct, fills = engine.evaluate(acct, {"AAPL": 94.0}, "t2", "2026-06-16")
    assert len(fills) == 1 and fills[0].fill_price == 95.0  # fills AT the limit
    assert acct.cash == 10000.0 - 950.0


def test_limit_sell_fills_at_or_above():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    acct, _ = _place(acct, symbol="AAPL", side="SELL", order_type="LIMIT",
                     quantity=10, limit_price=110.0)
    acct, fills = engine.evaluate(acct, {"AAPL": 109.0}, "t2", "2026-06-16")
    assert fills == []  # 109 < 110, resting
    acct, fills = engine.evaluate(acct, {"AAPL": 111.0}, "t3", "2026-06-16")
    assert len(fills) == 1 and fills[0].fill_price == 110.0
    assert "AAPL" not in acct.positions
    assert round(acct.realized_pnl, 2) == 100.0  # (110-100)*10


def test_avg_cost_weighting_across_buys():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 120.0}, "t2", "2026-06-16")
    assert acct.positions["AAPL"].quantity == 20
    assert acct.positions["AAPL"].avg_cost == 110.0  # (100*10 + 120*10)/20


def test_partial_sell_keeps_remaining_position():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    acct, _ = _place(acct, symbol="AAPL", side="SELL", order_type="MARKET", quantity=4)
    acct, _ = engine.evaluate(acct, {"AAPL": 130.0}, "t2", "2026-06-16")
    assert acct.positions["AAPL"].quantity == 6
    assert round(acct.realized_pnl, 2) == 120.0  # (130-100)*4
    assert acct.cash == 9000.0 + 4 * 130.0


def test_no_price_leaves_order_open():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=1)
    acct, fills = engine.evaluate(acct, {}, "t1", "2026-06-16")  # no price for AAPL
    assert fills == [] and len(acct.open_orders) == 1


def test_cancel_removes_open_order():
    acct = engine.open_account(10000.0, "t0")
    acct, order = _place(acct, symbol="AAPL", side="BUY", order_type="LIMIT",
                         quantity=1, limit_price=50.0)
    acct = engine.cancel_order(acct, order.paper_order_id)
    assert acct.open_orders == []
    assert acct.history[-1].status == "cancelled"
    assert engine.buying_power(acct) == 10000.0  # reservation released


def test_day_order_expires_on_later_et_date():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="LIMIT", quantity=1,
                     limit_price=50.0, today_et="2026-06-16")
    acct, fills = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-17")
    assert fills == [] and acct.open_orders == []
    assert acct.history[-1].status == "expired"


def test_gtc_order_survives_later_date():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="LIMIT", quantity=1,
                     limit_price=50.0, time_in_force="GTC", today_et="2026-06-16")
    acct, fills = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-20")
    assert fills == [] and len(acct.open_orders) == 1  # still resting, not expired


def test_account_view_reports_marks_and_buying_power():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    acct, _ = _place(acct, symbol="MSFT", side="BUY", order_type="LIMIT",
                     quantity=2, limit_price=50.0)  # rests, reserves 100
    view = engine.account_view(acct, {"AAPL": 110.0})
    assert view["cash"] == 9000.0
    assert view["buying_power"] == 9000.0 - 100.0  # MSFT limit reserves 100
    assert view["net_liquidation"] == 9000.0 + 10 * 110.0
    pos = {p["symbol"]: p for p in view["positions"]}
    assert pos["AAPL"]["market_value"] == 1100.0
    assert pos["AAPL"]["unrealized_pnl"] == 100.0  # (110-100)*10
    assert len(view["open_orders"]) == 1 and view["open_orders"][0]["symbol"] == "MSFT"


def test_account_view_handles_missing_price():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    view = engine.account_view(acct, {})  # no price
    pos = {p["symbol"]: p for p in view["positions"]}
    assert pos["AAPL"]["last"] is None
    assert pos["AAPL"]["market_value"] is None
    assert view["net_liquidation"] == 9000.0  # cash only when nothing is markable
    assert view["unmarked_positions"] == 1    # ...and the omission is disclosed


def test_account_view_unmarked_zero_when_all_priced():
    acct = engine.open_account(10000.0, "t0")
    acct, _ = _place(acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10)
    acct, _ = engine.evaluate(acct, {"AAPL": 100.0}, "t1", "2026-06-16")
    view = engine.account_view(acct, {"AAPL": 110.0})
    assert view["unmarked_positions"] == 0


def test_open_account_rejects_nonpositive_cash():
    with pytest.raises(engine.PaperError):
        engine.open_account(0.0, "t0")
    with pytest.raises(engine.PaperError):
        engine.open_account(-100.0, "t0")


def test_trim_history_caps_to_last_n():
    acct = engine.open_account(10000.0, "t0")
    for i in range(5):
        acct.history.append(PaperOrder(
            paper_order_id=f"o{i}", symbol="AAPL", side="BUY", order_type="MARKET",
            quantity=1, status="filled", created_at="t0", placed_et_date="2026-06-16"))
    engine.trim_history(acct, 3)
    assert len(acct.history) == 3
    assert [o.paper_order_id for o in acct.history] == ["o2", "o3", "o4"]  # kept the most recent


from webull_api.journal.schema import ThesisRecord


def test_place_order_with_thesis_round_trips():
    acct = engine.open_account(10000.0, "t0")
    thesis = ThesisRecord(confidence=4, setup="breakout")
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=1,
        limit_price=None, time_in_force="DAY", now_iso="t0",
        today_et="2026-06-16", last_price=100.0, thesis=thesis)
    assert order.thesis is not None
    assert order.thesis.confidence == 4
    assert order.thesis.setup == "breakout"
    # the order in open_orders carries it too
    assert acct.open_orders[0].thesis.confidence == 4


def test_place_order_without_thesis_is_none():
    acct = engine.open_account(10000.0, "t0")
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=1,
        limit_price=None, time_in_force="DAY", now_iso="t0",
        today_et="2026-06-16", last_price=100.0)
    assert order.thesis is None


# --- execution-fidelity queue plumbing (2026-07-27 spec) ---

def _acct(cash=100000.0):
    from webull_api.paper import engine
    return engine.open_account(cash, "t0")


def test_next_open_order_stores_policy_and_ref_price():
    from webull_api.paper import engine
    acct = _acct()
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t1", today_et="2026-07-27",
        last_price=200.0, fill_policy="next_open")
    assert order.fill_policy == "next_open"
    assert order.ref_price == 200.0
    assert order.fill_session is None
    assert order.status == "pending"


def test_default_fill_policy_is_immediate():
    from webull_api.paper import engine
    acct = _acct()
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t1", today_et="2026-07-27",
        last_price=200.0)
    assert order.fill_policy == "immediate"
    assert order.ref_price is None


def test_evaluate_skips_next_open_orders_entirely():
    """evaluate must neither fill NOR day-expire a queued order — a dashboard view at 8 PM
    (or the next evening) must leave it resting for settle."""
    from webull_api.paper import engine
    acct = _acct()
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t1", today_et="2026-07-27",
        last_price=200.0, fill_policy="next_open")
    # same-day evaluate: a MARKET order would normally fill instantly
    acct, fills = engine.evaluate(acct, {"AAPL": 201.0}, "t2", "2026-07-27")
    assert fills == []
    assert len(acct.open_orders) == 1
    # NEXT-day evaluate: a DAY order placed yesterday would normally expire
    acct, fills = engine.evaluate(acct, {"AAPL": 205.0}, "t3", "2026-07-28")
    assert fills == []
    assert len(acct.open_orders) == 1 and acct.open_orders[0].status == "pending"


def test_reserved_cash_counts_queued_market_buys():
    from webull_api.paper import engine
    acct = _acct(cash=3000.0)
    acct, _ = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t1", today_et="2026-07-27",
        last_price=200.0, fill_policy="next_open")
    assert engine.reserved_cash(acct) == 2000.0
    assert engine.buying_power(acct) == 1000.0
    # a second queued buy that would over-commit the remaining cash must be rejected at placement
    import pytest
    from webull_api.paper.schema import PaperError
    with pytest.raises(PaperError):
        engine.place_order(
            acct, symbol="MSFT", side="BUY", order_type="MARKET", quantity=10,
            limit_price=None, time_in_force="DAY", now_iso="t1", today_et="2026-07-27",
            last_price=200.0, fill_policy="next_open")


# --- settle_next_open (Task 2, 2026-07-27) ---

def _queued(acct, sym="AAPL", side="BUY", qty=10, last=200.0, day="2026-07-27"):
    from webull_api.paper import engine
    acct, order = engine.place_order(
        acct, symbol=sym, side=side, order_type="MARKET", quantity=qty,
        limit_price=None, time_in_force="DAY", now_iso=f"{day}T17:30:00", today_et=day,
        last_price=last, fill_policy="next_open")
    return acct, order


def test_settle_fills_at_first_open_after_placement():
    from webull_api.paper import engine
    acct = _acct()
    acct, order = _queued(acct, day="2026-07-27")
    bars = {"AAPL": [{"date": "2026-07-27", "open": 199.0},   # placement day — must NOT fill
                     {"date": "2026-07-28", "open": 204.5}]}
    acct, fills, rejected, resting = engine.settle_next_open(acct, bars, "2026-07-28T17:30:00")
    assert [o.paper_order_id for o in fills] == [order.paper_order_id]
    assert fills[0].fill_price == 204.5
    assert fills[0].fill_session == "2026-07-28"
    assert fills[0].status == "filled"
    assert acct.positions["AAPL"].avg_cost == 204.5
    assert acct.cash == 100000.0 - 10 * 204.5
    assert acct.open_orders == [] and rejected == [] and resting == []


def test_settle_missed_day_uses_historical_open_not_latest():
    """Placed Tue evening, PC off Wed, settle runs Thu: the fill is WEDNESDAY's open."""
    from webull_api.paper import engine
    acct = _acct()
    acct, _ = _queued(acct, day="2026-07-28")          # Tuesday evening
    bars = {"AAPL": [{"date": "2026-07-28", "open": 199.0},
                     {"date": "2026-07-29", "open": 190.0},   # Wednesday — the correct fill
                     {"date": "2026-07-30", "open": 210.0}]}  # Thursday (settle day)
    acct, fills, _, _ = engine.settle_next_open(acct, bars, "2026-07-30T17:30:00")
    assert fills[0].fill_price == 190.0
    assert fills[0].fill_session == "2026-07-29"


def test_settle_same_evening_is_noop():
    from webull_api.paper import engine
    acct = _acct()
    acct, _ = _queued(acct, day="2026-07-27")
    bars = {"AAPL": [{"date": "2026-07-27", "open": 199.0}]}  # no bar after placement yet
    acct, fills, rejected, resting = engine.settle_next_open(acct, bars, "2026-07-27T17:31:00")
    assert fills == [] and rejected == []
    assert len(resting) == 1 and len(acct.open_orders) == 1


def test_settle_missing_bars_leaves_order_resting():
    from webull_api.paper import engine
    acct = _acct()
    acct, _ = _queued(acct, day="2026-07-27")
    acct, fills, rejected, resting = engine.settle_next_open(acct, {}, "2026-07-28T17:30:00")
    assert fills == [] and rejected == [] and len(resting) == 1


def test_settle_buy_unaffordable_at_gap_up_open_rejects():
    from webull_api.paper import engine
    acct = _acct(cash=2050.0)
    acct, _ = _queued(acct, qty=10, last=200.0, day="2026-07-27")  # reserved 2000, cash 2050
    bars = {"AAPL": [{"date": "2026-07-28", "open": 215.0}]}       # now costs 2150 > cash
    acct, fills, rejected, resting = engine.settle_next_open(acct, bars, "2026-07-28T17:30:00")
    assert fills == [] and len(rejected) == 1
    assert rejected[0].status == "rejected"
    assert acct.open_orders == [] and acct.cash == 2050.0          # reservation released, nothing spent


def test_settle_sell_oversell_rejects():
    """A queued SELL whose position vanishes before settle (e.g. sold via another route)
    must reject at settle — the placement-time guard can't see the future."""
    from webull_api.paper import engine
    acct = _acct()
    acct, _ = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso="t0", today_et="2026-07-24",
        last_price=200.0)
    acct, _ = engine.evaluate(acct, {"AAPL": 200.0}, "t0", "2026-07-24")
    acct, _ = _queued(acct, side="SELL", qty=10, day="2026-07-27")
    del acct.positions["AAPL"]          # position vanishes between placement and settle
    bars = {"AAPL": [{"date": "2026-07-28", "open": 204.5}]}
    acct, fills, rejected, _ = engine.settle_next_open(acct, bars, "2026-07-28T17:30:00")
    assert fills == [] and len(rejected) == 1
    assert rejected[0].status == "rejected"


def test_settle_ignores_immediate_orders():
    """A manual resting LIMIT order must pass through settle untouched."""
    from webull_api.paper import engine
    acct = _acct()
    acct, order = engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="LIMIT", quantity=5,
        limit_price=150.0, time_in_force="GTC", now_iso="t1", today_et="2026-07-27",
        last_price=200.0)
    bars = {"AAPL": [{"date": "2026-07-28", "open": 204.5}]}
    acct, fills, rejected, resting = engine.settle_next_open(acct, bars, "2026-07-28T17:30:00")
    assert fills == [] and rejected == [] and resting == []
    assert [o.paper_order_id for o in acct.open_orders] == [order.paper_order_id]


def test_settle_processes_in_placement_order_cash_visible():
    """Two queued buys: the first consumes cash; the second rejects if the remainder can't cover it."""
    from webull_api.paper import engine
    acct = _acct(cash=4100.0)
    acct, o1 = _queued(acct, sym="AAPL", qty=10, last=200.0, day="2026-07-27")
    acct.open_orders[0].ref_price = 100.0  # shrink reservation so the 2nd placement passes
    acct, o2 = _queued(acct, sym="MSFT", qty=10, last=300.0, day="2026-07-27")
    bars = {"AAPL": [{"date": "2026-07-28", "open": 205.0}],   # costs 2050
            "MSFT": [{"date": "2026-07-28", "open": 300.0}]}   # costs 3000 > 4100-2050
    acct, fills, rejected, _ = engine.settle_next_open(acct, bars, "2026-07-28T17:30:00")
    assert [o.symbol for o in fills] == ["AAPL"]
    assert [o.symbol for o in rejected] == ["MSFT"]
