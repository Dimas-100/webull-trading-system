"""Tests for webull_api.paper.options_engine — TDD RED then GREEN."""
import pytest
from webull_api.paper import options_engine as eng
from webull_api.paper.options_schema import OptionsPaperError, OptionPaperOrder, OptionLeg
from webull_api.safety import OrderValidationError

C300 = "AAPL260717C00300000"
C310 = "AAPL260717C00310000"
P290 = "AAPL260717P00290000"
MARKS = {
    C300: {"bid": 5.0, "ask": 5.4, "last": 5.2, "mid": 5.2},
    C310: {"bid": 2.0, "ask": 2.4, "last": 2.2, "mid": 2.2},
    P290: {"bid": 1.5, "ask": 1.8, "last": 1.65, "mid": 1.65},
}


# ── open_account ─────────────────────────────────────────────────────────────

def test_open_account_sets_cash():
    acct = eng.open_account(10000.0, "2026-07-17T09:30:00")
    assert acct.cash == 10000.0
    assert acct.starting_cash == 10000.0
    assert acct.reserved_collateral == 0.0
    assert acct.positions == []
    assert acct.open_orders == []


def test_open_account_default_id():
    acct = eng.open_account(5000.0, "t0")
    assert acct.account_id == "options"


def test_open_account_custom_id():
    acct = eng.open_account(5000.0, "t0", account_id="paper-opts-1")
    assert acct.account_id == "paper-opts-1"


def test_open_account_raises_on_zero():
    with pytest.raises(OptionsPaperError):
        eng.open_account(0, "t0")


def test_open_account_raises_on_negative():
    with pytest.raises(OptionsPaperError):
        eng.open_account(-100.0, "t0")


# ── buying_power (no open orders, no collateral) ─────────────────────────────

def test_buying_power_fresh_account():
    acct = eng.open_account(10000.0, "t0")
    assert eng.buying_power(acct) == pytest.approx(10000.0)


# ── leg_objs ─────────────────────────────────────────────────────────────────

def test_leg_objs_parses_occ():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}])
    assert len(legs) == 1
    lg = legs[0]
    assert lg.underlying == "AAPL"
    assert lg.option_type == "CALL"
    assert lg.strike == pytest.approx(300.0)
    assert lg.expiration == "2026-07-17"
    assert lg.side == "BUY"
    assert lg.occ == C300


def test_leg_objs_put():
    legs = eng.leg_objs([{"occ": P290, "side": "SELL"}])
    assert legs[0].option_type == "PUT"
    assert legs[0].strike == pytest.approx(290.0)
    assert legs[0].side == "SELL"


def test_leg_objs_two_legs():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    assert len(legs) == 2
    assert legs[0].occ == C300
    assert legs[1].occ == C310


def test_leg_objs_invalid_occ_raises():
    with pytest.raises(OptionsPaperError):
        eng.leg_objs([{"occ": "NOTANOCC", "side": "BUY"}])


# ── net_price ─────────────────────────────────────────────────────────────────

def test_net_price_fill_buy_uses_ask():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}])
    result = eng.net_price(legs, MARKS, fill=True)
    assert result == pytest.approx(5.4)


def test_net_price_fill_sell_uses_bid():
    legs = eng.leg_objs([{"occ": C300, "side": "SELL"}])
    result = eng.net_price(legs, MARKS, fill=True)
    assert result == pytest.approx(-5.0)


def test_net_price_fill_uses_ask_for_buy_bid_for_sell():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    # buy ask 5.4 − sell bid 2.0 = 3.4 (net debit)
    assert eng.net_price(legs, MARKS, fill=True) == pytest.approx(3.4)


def test_net_price_mark_uses_mid():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    # mark: buy mid 5.2 − sell mid 2.2 = 3.0
    assert eng.net_price(legs, MARKS, fill=False) == pytest.approx(3.0)


def test_net_price_none_when_missing():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}])
    assert eng.net_price(legs, {}, fill=True) is None


def test_net_price_none_when_one_leg_missing():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    partial_marks = {C300: MARKS[C300]}  # C310 missing
    assert eng.net_price(legs, partial_marks, fill=True) is None


def test_net_price_credit_spread_negative():
    # sell C300, buy C310 → credit spread (credit = negative net)
    legs = eng.leg_objs([{"occ": C300, "side": "SELL"}, {"occ": C310, "side": "BUY"}])
    # fill: −bid(C300) + ask(C310) = −5.0 + 2.4 = −2.6
    assert eng.net_price(legs, MARKS, fill=True) == pytest.approx(-2.6)


# ── spread_width ─────────────────────────────────────────────────────────────

def test_spread_width_vertical():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    assert eng.spread_width(legs) == pytest.approx(10.0)


def test_spread_width_single():
    legs = eng.leg_objs([{"occ": C300, "side": "BUY"}])
    assert eng.spread_width(legs) == pytest.approx(0.0)


def test_spread_width_three_legs_returns_zero():
    # 3-leg combos not yet supported → returns 0 (safe: no width credit)
    legs = eng.leg_objs([
        {"occ": C300, "side": "BUY"},
        {"occ": C310, "side": "SELL"},
        {"occ": P290, "side": "SELL"},
    ])
    assert eng.spread_width(legs) == pytest.approx(0.0)


# ── risk_capital ──────────────────────────────────────────────────────────────

def test_risk_capital_debit():
    # long call: net 5.4, single, 1 contract
    assert eng.risk_capital(5.4, 0.0, 1) == pytest.approx(5.4 * 100)


def test_risk_capital_debit_spread():
    # debit call spread: net 3.4, width 10, 2 contracts
    assert eng.risk_capital(3.4, 10.0, 2) == pytest.approx(3.4 * 100 * 2)


def test_risk_capital_credit_spread():
    # credit spread: net -2.0, width 10, 1 contract → max loss = (10-2)*100
    assert eng.risk_capital(-2.0, 10.0, 1) == pytest.approx((10.0 - 2.0) * 100)


def test_risk_capital_zero_net_debit():
    # net = 0: free entry? Only possible if bid==ask==0; still 0 risk
    assert eng.risk_capital(0.0, 10.0, 1) == pytest.approx(0.0)


def test_risk_capital_qty_scales():
    assert eng.risk_capital(3.4, 10.0, 5) == pytest.approx(3.4 * 100 * 5)


# ── _open_reservation ─────────────────────────────────────────────────────────

def _make_open_order(intent: str, net, legs=None, qty=1) -> OptionPaperOrder:
    if legs is None:
        legs = [OptionLeg(occ=C300, underlying="AAPL", option_type="CALL",
                          strike=300.0, expiration="2026-07-17", side="BUY")]
    return OptionPaperOrder(
        paper_order_id="test-id",
        strategy="SINGLE",
        legs=legs,
        quantity=qty,
        intent=intent,
        order_type="LIMIT" if net is not None else "MARKET",
        limit_net_price=net,
        created_at="t0",
        placed_et_date="2026-07-17",
    )


def test_open_reservation_open_order_debit():
    o = _make_open_order("OPEN", 3.4)
    # single leg, width=0, net=3.4 → risk = 3.4*100*1
    assert eng._open_reservation(o) == pytest.approx(3.4 * 100)


def test_open_reservation_close_order_zero():
    o = _make_open_order("CLOSE", 3.4)
    assert eng._open_reservation(o) == pytest.approx(0.0)


def test_open_reservation_none_limit_net():
    # MARKET order for OPEN with no limit_net_price → 0
    o = _make_open_order("OPEN", None)
    assert eng._open_reservation(o) == pytest.approx(0.0)


def test_open_reservation_credit_order():
    # credit spread order: net -2.0, width 10 → (10-2)*100 reserved
    legs = [
        OptionLeg(occ=C300, underlying="AAPL", option_type="CALL",
                  strike=300.0, expiration="2026-07-17", side="SELL"),
        OptionLeg(occ=C310, underlying="AAPL", option_type="CALL",
                  strike=310.0, expiration="2026-07-17", side="BUY"),
    ]
    o = OptionPaperOrder(
        paper_order_id="test-id",
        strategy="VERTICAL",
        legs=legs,
        quantity=1,
        intent="OPEN",
        order_type="LIMIT",
        limit_net_price=-2.0,
        created_at="t0",
        placed_et_date="2026-07-17",
    )
    assert eng._open_reservation(o) == pytest.approx((10.0 - 2.0) * 100)


# ── buying_power with open orders / collateral ────────────────────────────────

def test_buying_power_with_open_order():
    acct = eng.open_account(10000.0, "t0")
    o = _make_open_order("OPEN", 3.4)  # reserves 340
    acct.open_orders.append(o)
    assert eng.buying_power(acct) == pytest.approx(10000.0 - 3.4 * 100)


def test_buying_power_with_collateral():
    acct = eng.open_account(10000.0, "t0")
    acct.reserved_collateral = 800.0
    assert eng.buying_power(acct) == pytest.approx(10000.0 - 800.0)


def test_buying_power_with_collateral_and_open_order():
    acct = eng.open_account(10000.0, "t0")
    acct.reserved_collateral = 800.0
    o = _make_open_order("OPEN", 3.4)  # 340
    acct.open_orders.append(o)
    assert eng.buying_power(acct) == pytest.approx(10000.0 - 800.0 - 340.0)


def test_buying_power_close_order_not_reserved():
    acct = eng.open_account(10000.0, "t0")
    o = _make_open_order("CLOSE", 1.5)
    acct.open_orders.append(o)
    assert eng.buying_power(acct) == pytest.approx(10000.0)


def test_open_account_and_buying_power():
    """Explicit test from the brief."""
    acct = eng.open_account(10000.0, "t0")
    assert acct.cash == 10000.0 and eng.buying_power(acct) == 10000.0
    with pytest.raises(OptionsPaperError):
        eng.open_account(0, "t0")


# ── place ─────────────────────────────────────────────────────────────────────

P300 = "AAPL260717P00300000"


def _place(acct, **kw):
    base = dict(quantity=1, intent="OPEN", order_type="MARKET",
                time_in_force="DAY", now_iso="t", today_et="2026-07-01", marks=MARKS)
    base.update(kw)
    return eng.place(acct, **base)


def test_place_single_long_ok_and_reserves_nothing_until_fill():
    acct = eng.open_account(10000.0, "t0")
    acct, o = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    assert o.status == "pending" and o.intent == "OPEN" and len(acct.open_orders) == 1


def test_place_rejects_naked_single_sell_to_open():
    acct = eng.open_account(10000.0, "t0")
    with pytest.raises(OptionsPaperError):
        _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}])


def test_place_vertical_validates_via_safety():
    acct = eng.open_account(10000.0, "t0")
    acct, o = _place(acct, strategy="VERTICAL",
                     legs=[{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    assert o.strategy == "VERTICAL" and len(o.legs) == 2


def test_place_vertical_rejects_mismatched_type():
    acct = eng.open_account(10000.0, "t0")
    with pytest.raises((OptionsPaperError, OrderValidationError)):  # validate_option_combo: both legs must be same type
        _place(acct, strategy="VERTICAL",
               legs=[{"occ": C300, "side": "BUY"}, {"occ": P300, "side": "SELL"}])


def test_place_rejects_insufficient_buying_power():
    acct = eng.open_account(100.0, "t0")   # debit 5.4*100 = 540 > 100
    with pytest.raises(OptionsPaperError):
        _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])


def test_place_close_rejects_unknown_unit():
    acct = eng.open_account(10000.0, "t0")
    with pytest.raises(OptionsPaperError):
        eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}], quantity=1,
                  intent="CLOSE", close_unit_id="does-not-exist", order_type="MARKET",
                  time_in_force="DAY", now_iso="t", today_et="2026-07-01", marks=MARKS)


# ── evaluate (Task 4) ─────────────────────────────────────────────────────────

def _fill(acct, today="2026-07-01"):
    return eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t", today_et=today)


def test_market_open_single_fills_at_ask_and_pays_debit():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, fills = _fill(acct)
    assert len(fills) == 1 and fills[0].fill_net_price == pytest.approx(5.4)
    assert acct.cash == pytest.approx(10000.0 - 5.4 * 100)
    assert len(acct.positions) == 1 and acct.positions[0].avg_net_price == pytest.approx(5.4)
    assert acct.positions[0].collateral == 0.0


def test_credit_vertical_fill_reserves_collateral_and_adds_credit():
    acct = eng.open_account(10000.0, "t0")
    # sell C300 (bid 5.0), buy C310 (ask 2.4) -> net = 2.4 − 5.0 = −2.6 (credit 2.6); width 10
    acct, _ = _place(acct, strategy="VERTICAL",
                     legs=[{"occ": C310, "side": "BUY"}, {"occ": C300, "side": "SELL"}])
    acct, fills = _fill(acct)
    assert fills[0].fill_net_price == pytest.approx(-2.6)
    assert acct.cash == pytest.approx(10000.0 + 2.6 * 100)           # credit received
    assert acct.reserved_collateral == pytest.approx((10.0 - 2.6) * 100)  # max loss held
    assert acct.positions[0].collateral == pytest.approx((10.0 - 2.6) * 100)


def test_limit_order_rests_until_net_crosses():
    acct = eng.open_account(10000.0, "t0")
    # want to BUY C300 for <= 5.0, but ask is 5.4 -> rest
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}],
                     order_type="LIMIT", limit_net_price=5.0)
    acct, fills = _fill(acct)
    assert fills == [] and len(acct.open_orders) == 1
    # ask drops to 5.0 -> fills at the limit
    better = {**MARKS, C300: {"bid": 4.8, "ask": 5.0, "last": 4.9, "mid": 4.9}}
    acct, fills = eng.evaluate(acct, marks=better, spots={}, now_iso="t", today_et="2026-07-01")
    assert len(fills) == 1 and fills[0].fill_net_price == pytest.approx(5.0)


def test_day_order_expires_on_date_rollover():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}],
                     order_type="LIMIT", limit_net_price=1.0)  # never fills
    acct, fills = _fill(acct, today="2026-07-02")  # next day
    assert fills == [] and acct.open_orders == [] and acct.history[-1].status == "expired"


# ── _apply_close (Task 5) ─────────────────────────────────────────────────────

def test_close_long_single_realizes_pnl():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)                              # bought at 5.4
    unit = acct.positions[0]
    # close (sell-to-close): bid rises to 7.0 -> sell-leg fill uses bid 7.0 -> close_net = −7.0
    rich = {**MARKS, C300: {"bid": 7.0, "ask": 7.4, "last": 7.2, "mid": 7.2}}
    acct, o = eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}],
                        quantity=1, intent="CLOSE", close_unit_id=unit.unit_id,
                        order_type="MARKET", time_in_force="DAY",
                        now_iso="t", today_et="2026-07-01", marks=rich)
    acct, fills = eng.evaluate(acct, marks=rich, spots={}, now_iso="t", today_et="2026-07-01")
    assert acct.positions == []                        # fully closed
    assert acct.realized_pnl == pytest.approx((7.0 - 5.4) * 100)   # value(−close_net=7.0) − avg(5.4)
    assert acct.cash == pytest.approx(10000.0 - 5.4 * 100 + 7.0 * 100)


def test_partial_close_reduces_qty_and_releases_collateral():
    acct = eng.open_account(20000.0, "t0")
    # credit vertical, qty 2: collateral = (10−2.6)*100*2 = 1480
    acct, _ = _place(acct, strategy="VERTICAL", quantity=2,
                     legs=[{"occ": C310, "side": "BUY"}, {"occ": C300, "side": "SELL"}])
    acct, _ = _fill(acct)
    unit = acct.positions[0]
    assert acct.reserved_collateral == pytest.approx((10 - 2.6) * 100 * 2)
    # close 1 of 2 contracts (buy back the spread): buy C300 ask 5.4, sell C310 bid 2.0 -> net +3.4
    acct, _ = eng.place(acct, strategy="VERTICAL", quantity=1, intent="CLOSE",
                        close_unit_id=unit.unit_id, order_type="MARKET", time_in_force="DAY",
                        now_iso="t", today_et="2026-07-01", marks=MARKS,
                        legs=[{"occ": C300, "side": "BUY"}, {"occ": C310, "side": "SELL"}])
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t", today_et="2026-07-01")
    assert acct.positions[0].quantity == 1
    assert acct.reserved_collateral == pytest.approx((10 - 2.6) * 100 * 1)   # half released


def test_place_close_rejects_quantity_over_open():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    unit = acct.positions[0]
    with pytest.raises(OptionsPaperError):
        eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}],
                  quantity=2, intent="CLOSE", close_unit_id=unit.unit_id,
                  order_type="MARKET", time_in_force="DAY",
                  now_iso="t", today_et="2026-07-01", marks=MARKS)


# ── _settle_expirations (Task 6) ──────────────────────────────────────────────

def test_itm_long_call_settles_to_intrinsic():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)                              # bought at 5.4, strike 300
    # next day, expired; underlying at 320 -> intrinsic 20
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 320.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []
    assert acct.realized_pnl == pytest.approx((20.0 - 5.4) * 100)
    assert acct.history[-1].status == "expired"


def test_otm_long_call_expires_worthless():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 250.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == [] and acct.realized_pnl == pytest.approx((0.0 - 5.4) * 100)


def test_credit_spread_settles_both_legs():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="VERTICAL",
                     legs=[{"occ": C310, "side": "BUY"}, {"occ": C300, "side": "SELL"}])  # net −2.6
    acct, _ = _fill(acct)
    # underlying 320: long C310 intrinsic 10, short C300 intrinsic 20 -> value = 10 − 20 = −10
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 320.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []
    assert acct.realized_pnl == pytest.approx((-10.0 - (-2.6)) * 100)   # value − avg_net
    assert acct.reserved_collateral == pytest.approx(0.0)              # collateral released


def test_settle_skipped_when_spot_missing():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={}, now_iso="t", today_et="2026-07-18")
    assert len(acct.positions) == 1   # not settled (no spot) — rests for next cycle


# ── settlement spot lookup: universe symbol form ("BRK B") vs OCC-parsed root ("BRKB") ────
# roadmap #9 follow-up: OptionLeg.underlying is always the OCC-parsed root (occ_root(sym)),
# but callers building the spots/settle_spots dicts (e.g. options_entry_service.py's
# opt_svc.spots_for({sym})) key them off the UNIVERSE form, which for a share class like
# Berkshire B spells the same company "BRK B" (a space). Both forms must resolve.

BRKB_C500 = "BRKB260717C00500000"
BRKB_MARKS = {BRKB_C500: {"bid": 30.0, "ask": 30.4, "last": 30.2, "mid": 30.2}}


def _open_brkb_unit(acct):
    acct, _ = eng.place(acct, strategy="SINGLE", legs=[{"occ": BRKB_C500, "side": "BUY"}],
                        quantity=1, intent="OPEN", order_type="MARKET", time_in_force="DAY",
                        now_iso="t", today_et="2026-07-01", marks=BRKB_MARKS)
    acct, _ = eng.evaluate(acct, marks=BRKB_MARKS, spots={}, now_iso="t", today_et="2026-07-01")
    assert acct.positions[0].legs[0].underlying == "BRKB"   # OCC-parsed, no space
    return acct


def test_settle_resolves_spot_keyed_by_universe_symbol_form():
    acct = _open_brkb_unit(eng.open_account(10000.0, "t0"))
    # next day, expired; spot dict keyed "BRK B" (the space form spots_for({sym}) would produce)
    acct, _ = eng.evaluate(acct, marks=BRKB_MARKS, spots={"BRK B": 520.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []            # settled, not deferred to next cycle
    assert acct.realized_pnl == pytest.approx((20.0 - 30.4) * 100)


def test_settle_resolves_spot_keyed_by_occ_root_form_too():
    # the OCC-root form must keep working too (existing single-word tickers rely on this).
    acct = _open_brkb_unit(eng.open_account(10000.0, "t0"))
    acct, _ = eng.evaluate(acct, marks=BRKB_MARKS, spots={"BRKB": 520.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []
    assert acct.realized_pnl == pytest.approx((20.0 - 30.4) * 100)


def test_settle_resolves_exp_close_keyed_by_universe_symbol_form():
    acct = _open_brkb_unit(eng.open_account(10000.0, "t0"))
    acct, _ = eng.evaluate(acct, marks=BRKB_MARKS, spots={}, settle_spots={("BRK B", "2026-07-17"): 520.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []
    assert acct.realized_pnl == pytest.approx((20.0 - 30.4) * 100)
    assert acct.history[-1].settle_basis == "exp_close"


# ── cancel (Task 7) ───────────────────────────────────────────────────────────

def test_cancel_removes_open_order():
    acct = eng.open_account(10000.0, "t0")
    acct, o = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}],
                     order_type="LIMIT", limit_net_price=1.0)
    acct = eng.cancel(acct, o.paper_order_id)
    assert acct.open_orders == [] and acct.history[-1].status == "cancelled"


# ── account_view (Task 7) ─────────────────────────────────────────────────────

def test_account_view_marks_at_mid():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)                              # bought at 5.4; mid is 5.2
    view = eng.account_view(acct, MARKS)
    pos = view["positions"][0]
    assert pos["mark_net"] == pytest.approx(5.2)
    assert pos["unrealized_pnl"] == pytest.approx((5.2 - 5.4) * 100)   # value(−(−mid)=mid) − avg
    assert view["net_liquidation"] == pytest.approx(acct.cash + 5.2 * 100)
    assert view["buying_power"] == pytest.approx(acct.cash)            # no collateral, no open orders


# ── evaluate rejects a CLOSE whose unit is gone (never raises → no 400 wedge) ──

def test_resting_close_after_expiration_settles_is_rejected_not_raised():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)                              # long C300 @ 5.4
    unit = acct.positions[0]
    # GTC LIMIT close that does NOT cross today (wants net <= −9.0; bid 5.0 → close net −5.0)
    acct, o = eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}],
                        quantity=1, intent="CLOSE", close_unit_id=unit.unit_id,
                        order_type="LIMIT", limit_net_price=-9.0, time_in_force="GTC",
                        now_iso="t", today_et="2026-07-01", marks=MARKS)
    # Past expiration: the unit settles to intrinsic in the same pass, THEN the close order
    # crosses (bid 9.5 → close net −9.5 ≤ −9.0) against a unit that's gone → rejected, no raise.
    rich = {**MARKS, C300: {"bid": 9.5, "ask": 9.9, "last": 9.7, "mid": 9.7}}
    acct, fills = eng.evaluate(acct, marks=rich, spots={"AAPL": 320.0},
                               now_iso="t2", today_et="2026-07-18")
    assert acct.open_orders == []
    rejected = [h for h in acct.history if h.status == "rejected"]
    assert len(rejected) == 1 and rejected[0].paper_order_id == o.paper_order_id
    assert rejected[0].reject_reason
    assert acct.positions == [] and any(h.status == "expired" for h in acct.history)


def test_two_closes_on_one_unit_second_is_rejected_not_raised():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    unit = acct.positions[0]
    close_kw = dict(strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}], quantity=1,
                    intent="CLOSE", close_unit_id=unit.unit_id, order_type="LIMIT",
                    time_in_force="DAY", now_iso="t", today_et="2026-07-01", marks=MARKS)
    acct, o1 = eng.place(acct, limit_net_price=-4.0, **close_kw)   # crosses (close net −5.0)
    acct, o2 = eng.place(acct, limit_net_price=-4.0, **close_kw)
    acct, fills = _fill(acct)                                       # both cross in one pass
    assert len(fills) == 1 and fills[0].paper_order_id == o1.paper_order_id
    assert acct.positions == [] and acct.open_orders == []
    rejected = [h for h in acct.history if h.status == "rejected"]
    assert len(rejected) == 1 and rejected[0].paper_order_id == o2.paper_order_id
    assert rejected[0].reject_reason
    # a subsequent refresh must be clean (the wedge is gone)
    acct, fills2 = _fill(acct)
    assert fills2 == []


# ── place rejects a LIMIT CLOSE without a price (would TypeError on every refresh) ──

def test_place_close_limit_without_price_rejected():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    unit = acct.positions[0]
    with pytest.raises(OptionsPaperError):
        eng.place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "SELL"}], quantity=1,
                  intent="CLOSE", close_unit_id=unit.unit_id, order_type="LIMIT",
                  limit_net_price=None, time_in_force="DAY", now_iso="t",
                  today_et="2026-07-01", marks=MARKS)


# ── _apply_open clamps collateral at 0 (mirror of risk_capital) ────────────────

C305 = "AAPL260717C00305000"


def test_credit_fill_beyond_width_clamps_collateral_to_zero():
    """5-wide credit vertical filled at net −6.00: |credit| > width must NOT book NEGATIVE
    collateral (which would inflate buying power) — clamps to 0 like risk_capital."""
    acct = eng.open_account(10000.0, "t0")
    wide = {C300: {"bid": 8.0, "ask": 8.4, "last": 8.2, "mid": 8.2},
            C305: {"bid": 1.8, "ask": 2.0, "last": 1.9, "mid": 1.9}}
    acct, _ = eng.place(acct, strategy="VERTICAL",
                        legs=[{"occ": C305, "side": "BUY"}, {"occ": C300, "side": "SELL"}],
                        quantity=1, intent="OPEN", order_type="MARKET", time_in_force="DAY",
                        now_iso="t", today_et="2026-07-01", marks=wide)
    acct, fills = eng.evaluate(acct, marks=wide, spots={}, now_iso="t", today_et="2026-07-01")
    assert fills[0].fill_net_price == pytest.approx(-6.0)   # ask 2.0 − bid 8.0
    assert acct.positions[0].collateral == pytest.approx(0.0)
    assert acct.reserved_collateral == pytest.approx(0.0)
    assert acct.cash == pytest.approx(10000.0 + 6.0 * 100)
    assert eng.buying_power(acct) == pytest.approx(acct.cash)   # NOT inflated past cash


# ── expiration settlement price basis (injectable exp-day close + stamp) ──────

def test_settle_uses_exp_close_when_supplied():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)                              # long C300 @ 5.4
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 250.0},
                           settle_spots={("AAPL", "2026-07-17"): 320.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.positions == []
    assert acct.realized_pnl == pytest.approx((20.0 - 5.4) * 100)   # intrinsic at the EXP close
    assert acct.history[-1].settle_basis == "exp_close"


def test_settle_falls_back_to_current_spot_and_stamps_basis():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    acct, _ = eng.evaluate(acct, marks=MARKS, spots={"AAPL": 320.0},
                           now_iso="t", today_et="2026-07-18")
    assert acct.realized_pnl == pytest.approx((20.0 - 5.4) * 100)
    assert acct.history[-1].settle_basis == "current_spot"


# ── account_view discloses positions omitted from net_liquidation ─────────────

def test_account_view_discloses_unmarked_positions():
    acct = eng.open_account(10000.0, "t0")
    acct, _ = _place(acct, strategy="SINGLE", legs=[{"occ": C300, "side": "BUY"}])
    acct, _ = _fill(acct)
    view = eng.account_view(acct, {})                  # no marks at all
    assert view["unmarked_positions"] == 1
    assert view["net_liquidation"] == pytest.approx(acct.cash)   # gap disclosed, not hidden
    marked = eng.account_view(acct, MARKS)
    assert marked["unmarked_positions"] == 0
