# tests/test_autopilot_gate.py
from webull_api import safety
from webull_api.autopilot.config import AutopilotConfig
from webull_api.autopilot.gate import authorize, GateState, Decision


def _cfg(**kw):
    base = dict(enabled=True, max_notional=40.0, max_positions=1, max_orders_per_day=5,
                daily_loss_halt=40.0, max_positions_risk_off=0)
    base.update(kw)
    return AutopilotConfig(**base)


def _state(**kw):
    base = dict(kill_active=False, in_window=True, open_position_symbols=frozenset(),
                orders_today=0, day_pl=0.0, spy_risk_off=False, last_price=None)
    base.update(kw)
    return GateState(**base)


def _buy(symbol="AAPL", qty="1", limit="30"):
    return safety.build_order(symbol=symbol, side="BUY", quantity=qty,
                              order_type="LIMIT", limit_price=limit, time_in_force="GTC")


def _sell_stop(symbol="AAPL", qty="1", stop="20"):
    return safety.build_order(symbol=symbol, side="SELL", quantity=qty,
                              order_type="STOP_LOSS", stop_price=stop, time_in_force="GTC")


# ---- universal layers ----
def test_disabled_denies_everything():
    d = authorize(_buy(), side="BUY", state=_state(), cfg=_cfg(enabled=False))
    assert d.allow is False and d.layer == "enable"


def test_kill_active_denies_buy_and_sell():
    st = _state(kill_active=True)
    assert authorize(_buy(), side="BUY", state=st, cfg=_cfg()).layer == "kill"
    assert authorize(_sell_stop(), side="SELL", state=st, cfg=_cfg()).layer == "kill"


def test_outside_window_denies():
    d = authorize(_buy(), side="BUY", state=_state(in_window=False), cfg=_cfg())
    assert d.allow is False and d.layer == "window"


# ---- risk-reducing SELLs bypass the caps ----
def test_protective_sell_allowed_even_at_position_cap_and_halt():
    st = _state(open_position_symbols=frozenset({"AAPL", "MSFT"}), orders_today=99, day_pl=-500.0)
    d = authorize(_sell_stop(), side="SELL", state=st, cfg=_cfg())
    assert d.allow is True and d.layer == "risk-reducing"


def test_buy_denied_in_same_state_where_sell_allowed():
    st = _state(open_position_symbols=frozenset({"MSFT"}), day_pl=-500.0)
    assert authorize(_buy(), side="BUY", state=st, cfg=_cfg()).allow is False


# ---- BUY cap stack ----
def test_buy_within_all_caps_allows():
    d = authorize(_buy(qty="1", limit="30"), side="BUY", state=_state(), cfg=_cfg())
    assert d.allow is True and d.layer == "ok"


def test_buy_over_notional_cap_denies():
    d = authorize(_buy(qty="2", limit="30"), side="BUY", state=_state(), cfg=_cfg(max_notional=40))
    assert d.allow is False and d.layer == "cap"


def test_buy_notional_exactly_at_cap_allows():
    d = authorize(_buy(qty="1", limit="40"), side="BUY", state=_state(), cfg=_cfg(max_notional=40))
    assert d.allow is True


def test_buy_unpriceable_denies_fail_closed():
    order = safety.build_order(symbol="AAPL", side="BUY", quantity="1", order_type="MARKET")
    d = authorize(order, side="BUY", state=_state(last_price=None), cfg=_cfg())
    assert d.allow is False and d.layer == "cap"


def test_buy_at_position_cap_denies():
    st = _state(open_position_symbols=frozenset({"MSFT"}))
    d = authorize(_buy(symbol="AAPL"), side="BUY", state=st, cfg=_cfg(max_positions=1))
    assert d.allow is False and d.layer == "positions"


def test_buy_already_held_symbol_denies_no_averaging():
    st = _state(open_position_symbols=frozenset({"AAPL"}))
    d = authorize(_buy(symbol="AAPL"), side="BUY", state=st, cfg=_cfg(max_positions=3))
    assert d.allow is False and d.layer == "positions"


def test_buy_at_daily_order_cap_denies():
    d = authorize(_buy(), side="BUY", state=_state(orders_today=5), cfg=_cfg(max_orders_per_day=5))
    assert d.allow is False and d.layer == "orders_per_day"


def test_buy_daily_loss_halt_denies():
    d = authorize(_buy(), side="BUY", state=_state(day_pl=-40.0), cfg=_cfg(daily_loss_halt=40))
    assert d.allow is False and d.layer == "halt"


def test_buy_day_pl_unknown_denies_fail_closed():
    d = authorize(_buy(), side="BUY", state=_state(day_pl=None), cfg=_cfg())
    assert d.allow is False and d.layer == "halt"


def test_risk_off_position_cap_enforced():
    st = _state(spy_risk_off=True, open_position_symbols=frozenset())
    d = authorize(_buy(), side="BUY", state=st, cfg=_cfg(max_positions=1, max_positions_risk_off=0))
    assert d.allow is False and d.layer == "positions"


def test_malformed_order_denies_validate_layer():
    bad = {"symbol": "AAPL", "side": "BUY", "order_type": "LIMIT", "quantity": "0", "time_in_force": "GTC"}
    d = authorize(bad, side="BUY", state=_state(), cfg=_cfg())
    assert d.allow is False and d.layer == "validate"


def test_buy_order_labeled_sell_denied_side_mismatch():
    # A real BUY order must not sneak through the risk-reducing SELL bypass.
    d = authorize(_buy(), side="SELL", state=_state(), cfg=_cfg())
    assert d.allow is False and d.layer == "validate"


def test_buy_denied_when_halt_latched_even_if_pl_recovered():
    d = authorize(_buy(), side="BUY", state=_state(halt_tripped=True, day_pl=0.0), cfg=_cfg())
    assert d.allow is False and d.layer == "halt"


def test_sell_still_allowed_when_halt_latched():
    d = authorize(_sell_stop(), side="SELL", state=_state(halt_tripped=True), cfg=_cfg())
    assert d.allow is True
