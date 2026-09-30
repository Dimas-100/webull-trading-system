import pytest

from webull_api import safety
from webull_api.safety import OrderValidationError

C295 = "AAPL260717C00295000"
C300 = "AAPL260717C00300000"
P295 = "AAPL260717P00295000"


def L(symbol, side, qty="1", price="5.00", ot="LIMIT"):
    return safety.build_option_leg(
        symbol=symbol, side=side, quantity=qty,
        limit_price=(None if ot == "MARKET" else price), order_type=ot,
    )


def single(**o):
    kw = {"symbol": C295, "side": "BUY", "quantity": "1", "limit_price": "5.00"}
    kw.update(o)
    return safety.build_option_combo(strategy="SINGLE", legs=[safety.build_option_leg(**kw)])


def vertical(legs):
    return safety.build_option_combo(strategy="VERTICAL", legs=legs)


def test_build_combo_shape():
    c = single()
    assert c["combo_type"] == "NORMAL" and c["option_strategy"] == "SINGLE"
    assert c["side"] == "BUY" and c["limit_price"] == "5.00" and "client_order_id" in c
    leg = c["orders"][0]
    # the OCC is expanded to the underlying + option fields the live API wants
    assert leg["symbol"] == "AAPL" and leg["option_type"] == "CALL" and leg["strike_price"] == "295"
    assert leg["instrument_super_type"] == "OPTION" and leg["instrument_type"] == "OPTION"
    assert leg["init_exp_date"] == "2026-07-17"


def test_single_valid():
    assert safety.validate_option_combo(single()) is not None


def test_option_fatfinger_guard_skips_without_reference_price():
    """No reference price -> guard cannot fire (best-effort), so an absurd limit still validates.
    confirm=True is only the submit flag, not a price check; the downstream options notional limit
    is the autopilot gate's authorize_option debit cap (cfg.opt_max_debit)."""
    assert safety.validate_option_combo(single(limit_price="500.00")) is not None


def test_option_fatfinger_guard_rejects_absurd_limit_with_reference():
    """With a reference last price, a wildly-off LIMIT leg (>70%) is rejected."""
    combo = single(limit_price="500.00")  # ~$500 on a contract worth ~$5
    leg_id = combo["orders"][0]["client_order_id"]
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(combo, last_by_leg={leg_id: 5.0})


def test_option_fatfinger_guard_passes_reasonable_limit_with_reference():
    combo = single(limit_price="5.20")
    leg_id = combo["orders"][0]["client_order_id"]
    assert safety.validate_option_combo(combo, last_by_leg={leg_id: 5.0}) is not None


def test_option_fatfinger_guard_skips_leg_without_a_price():
    # A reference map that doesn't include this leg's id -> skipped (never blocks).
    combo = single(limit_price="500.00")
    assert safety.validate_option_combo(combo, last_by_leg={"other-id": 5.0}) is not None


def test_single_rejects_bad():
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(single(side="HOLD"))
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(single(quantity="0"))
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(single(quantity="1.5"))
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(single(symbol="NOTOCC"))
    bad = single()
    bad["orders"][0]["limit_price"] = None
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(bad)  # LIMIT needs a price


def test_single_requires_one_leg():
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(
            safety.build_option_combo(strategy="SINGLE", legs=[L(C295, "BUY"), L(C300, "SELL")]))


def test_vertical_valid_debit_call_spread():
    assert safety.validate_option_combo(vertical([L(C295, "BUY"), L(C300, "SELL")])) is not None


def test_vertical_rejects_violations():
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY")]))                  # needs 2 legs
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY"), L(P295, "SELL")]))  # type mismatch
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY"), L(C295, "SELL")]))  # same strike
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY"), L(C300, "BUY")]))   # not one buy/one sell
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY", "1"), L(C300, "SELL", "2")]))  # unequal qty
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(vertical([L(C295, "BUY"), L("MSFT260717C00400000", "SELL")]))  # diff underlying


def test_bad_strategy():
    with pytest.raises(OrderValidationError):
        safety.validate_option_combo(
            safety.build_option_combo(strategy="IRON_CONDOR", legs=[L(C295, "BUY")]))
