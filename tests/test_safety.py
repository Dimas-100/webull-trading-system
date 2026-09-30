import pytest
from webull_api import safety
from webull_api.safety import OrderValidationError


def test_build_order_defaults():
    o = safety.build_order(symbol="aapl", side="BUY", quantity=2, limit_price=100)
    assert o["symbol"] == "AAPL"
    assert o["combo_type"] == "NORMAL"
    assert o["instrument_type"] == "EQUITY" and o["market"] == "US"
    assert o["entrust_type"] == "QTY" and o["support_trading_session"] == "CORE"
    assert o["quantity"] == "2" and o["limit_price"] == "100"
    assert len(o["client_order_id"]) <= 32 and o["client_order_id"]


def test_build_market_order_has_no_limit_price():
    o = safety.build_order(symbol="AAPL", side="SELL", quantity=1, order_type="MARKET")
    assert "limit_price" not in o


def test_validate_accepts_good_limit_order():
    o = safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=100)
    assert safety.validate_order(o, last_price=101) is o


@pytest.mark.parametrize("bad", [
    {"side": "HODL", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "A", "quantity": "1", "limit_price": "1"},
    {"side": "BUY", "order_type": "FUNKY", "time_in_force": "DAY", "symbol": "A", "quantity": "1", "limit_price": "1"},
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "WHENEVER", "symbol": "A", "quantity": "1", "limit_price": "1"},
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "", "quantity": "1", "limit_price": "1"},
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "A", "quantity": "0", "limit_price": "1"},
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "A", "quantity": "-3", "limit_price": "1"},
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "A", "quantity": "1"},  # missing limit
    {"side": "BUY", "order_type": "LIMIT", "time_in_force": "DAY", "symbol": "A", "quantity": "1", "limit_price": "0"},
])
def test_validate_rejects_bad_orders(bad):
    with pytest.raises(OrderValidationError):
        safety.validate_order(bad)


def test_validate_rejects_wild_limit_price():
    o = safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=1000)
    with pytest.raises(OrderValidationError):
        safety.validate_order(o, last_price=100)  # 900% away > 20% guard


def test_validate_allows_wild_price_with_wider_guard():
    o = safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=1000)
    assert safety.validate_order(o, last_price=100, max_price_deviation=20.0) is o


# Ported 2026-09-28 from the archived web order-ticket tests (test_web_api.py), their only coverage.
def test_build_order_time_in_force_passes_through_and_defaults_to_day():
    assert safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=100,
                              time_in_force="GTC")["time_in_force"] == "GTC"
    assert safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=100)["time_in_force"] == "DAY"


def test_deviation_guard_is_dormant_without_a_reference_price():
    # No reference price -> the fat-finger guard cannot fire (a data outage never blocks an order).
    o = safety.build_order(symbol="AAPL", side="BUY", quantity=1, limit_price=1000)
    assert safety.validate_order(o, last_price=None) is o


def test_should_submit_gate():
    assert safety.should_submit("test", confirm=False) is False
    assert safety.should_submit("prod", confirm=False) is False
    assert safety.should_submit("test", confirm=True) is True
    assert safety.should_submit("prod", confirm=True) is True
