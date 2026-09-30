"""authorize_option — the unattended option-placement wall. Every wall must individually deny;
the existing authorize() for equities is untouched by this feature (asserted at the bottom)."""
from webull_api import safety
from webull_api.autopilot.config import AutopilotConfig
from webull_api.autopilot.gate import GateState, authorize, authorize_option

OCC = "F260918C00014000"  # F Sep 18 2026 $14 call


def _combo(price="0.50", qty="1"):
    leg = safety.build_option_leg(symbol=OCC, side="BUY", quantity=qty, limit_price=price)
    return safety.build_option_combo(strategy="SINGLE", legs=[leg])


def _cfg(**kw):
    base = dict(enabled=True, decisions_enabled=True, opt_max_debit=70.0, opt_max_open=2)
    base.update(kw)
    return AutopilotConfig(**base)


def _state(**kw):
    base = dict(kill_active=False, in_window=True, open_position_symbols=frozenset(),
                orders_today=0, day_pl=0.0, spy_risk_off=False)
    base.update(kw)
    return GateState(**base)


def test_allows_inside_all_walls():
    d = authorize_option(_combo(), state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is True and d.layer == "ok"


def test_universal_walls_block():
    assert authorize_option(_combo(), state=_state(), cfg=_cfg(enabled=False), open_option_units=0).allow is False
    assert authorize_option(_combo(), state=_state(kill_active=True), cfg=_cfg(), open_option_units=0).allow is False
    assert authorize_option(_combo(), state=_state(), cfg=_cfg(decisions_enabled=False), open_option_units=0).allow is False
    assert authorize_option(_combo(), state=_state(in_window=False), cfg=_cfg(), open_option_units=0).allow is False


def test_validation_wall_blocks_malformed():
    broken = _combo()
    broken["orders"][0]["quantity"] = "1.5"
    d = authorize_option(broken, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "validate"


SHORT_OCC = "F260918C00015000"  # F Sep 18 2026 $15 call


def _vertical(long_price="0.47", short_price="0.14", long_occ=OCC, short_occ=SHORT_OCC,
              long_side="BUY", short_side="SELL"):
    return safety.build_option_combo(strategy="VERTICAL", legs=[
        safety.build_option_leg(symbol=long_occ, side=long_side, quantity="1",
                                limit_price=long_price),
        safety.build_option_leg(symbol=short_occ, side=short_side, quantity="1",
                                limit_price=short_price),
    ])


def test_debit_vertical_is_allowed():
    d = authorize_option(_vertical(), state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is True and d.layer == "ok"


def test_credit_vertical_denied_by_the_buy_wall():
    """Net credit -> build_option_combo sets side SELL -> the debit-only wall kills it."""
    credit = _vertical(long_price="0.14", short_price="0.47")   # paying less than received
    assert credit["side"] == "SELL"
    d = authorize_option(credit, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "structure"


NEAR_OCC = "F260918C00014500"  # $14.50 call -> a 0.50-wide spread against the $14 long


def test_vertical_debit_must_be_under_the_width():
    """0.50-wide spread at a 0.55 net debit ($55, inside the $70 cap) can never profit."""
    wide = _vertical(long_price="0.70", short_price="0.15", short_occ=NEAR_OCC)
    assert float(wide["limit_price"]) == 0.55
    d = authorize_option(wide, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "structure"


def test_vertical_edge_filter_on_fraction_of_width():
    """0.70 net on a 1.00 width = 70% of width — inside the dollar cap, over the edge ceiling."""
    pricey = _vertical(long_price="0.85", short_price="0.15")
    assert float(pricey["limit_price"]) == 0.70
    d = authorize_option(pricey, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "edge"


def test_exotic_strategy_rejected():
    """Anything beyond SINGLE/VERTICAL dies — at the shared validator, before my walls."""
    combo = _vertical()
    combo["option_strategy"] = "IRON_CONDOR"
    d = authorize_option(combo, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "validate"


def test_two_buy_legs_rejected_by_validation():
    both = _vertical(short_side="BUY")
    d = authorize_option(both, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "validate"


def test_single_leg_credit_still_denied():
    credit = _combo()
    credit["side"] = "SELL"
    d2 = authorize_option(credit, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d2.allow is False


def test_cap_walls_block():
    over = _combo(price="0.90")   # $90 > $70 cap
    d = authorize_option(over, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "cap"
    assert authorize_option(_combo(), state=_state(), cfg=_cfg(), open_option_units=2).allow is False
    assert authorize_option(_combo(), state=_state(orders_today=5), cfg=_cfg(), open_option_units=0).allow is False


def test_halt_walls_failclosed():
    assert authorize_option(_combo(), state=_state(day_pl=None), cfg=_cfg(), open_option_units=0).allow is False
    assert authorize_option(_combo(), state=_state(day_pl=-40.0), cfg=_cfg(), open_option_units=0).allow is False
    assert authorize_option(_combo(), state=_state(halt_tripped=True), cfg=_cfg(), open_option_units=0).allow is False


def test_risk_off_regime_blocks_option_buys():
    d = authorize_option(_combo(), state=_state(spy_risk_off=True), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer == "regime"


def test_nan_limit_price_cannot_slip_the_cap():
    """Finding C1: nan > cap is always False — the gate must read NaN as unpriceable and deny."""
    nan_combo = _combo()
    nan_combo["limit_price"] = "nan"
    d = authorize_option(nan_combo, state=_state(), cfg=_cfg(), open_option_units=0)
    assert d.allow is False and d.layer in ("validate", "cap")


def test_equity_authorize_unchanged_by_new_config_defaults():
    order = safety.build_order(symbol="AAPL", side="BUY", quantity="1",
                               order_type="LIMIT", limit_price="10")
    d = authorize(order, side="BUY", state=_state(last_price=10.0), cfg=_cfg())
    assert d.allow is True
