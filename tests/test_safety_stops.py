"""Buy-stop / stop-limit order validation (the gate widening). The submit rails are
unchanged; these cover the new order-TYPE branches only."""
import pytest

from webull_api import safety
from webull_api.safety import OrderValidationError as OVE


def _o(**kw):
    base = dict(symbol="AAPL", side="BUY", quantity="1", order_type="STOP_LOSS")
    base.update(kw)
    return safety.build_order(**base)


def test_build_order_includes_stop_price():
    o = _o(stop_price="105")
    assert o["order_type"] == "STOP_LOSS" and o["stop_price"] == "105"
    assert "limit_price" not in o


def test_stop_loss_requires_positive_stop_price():
    with pytest.raises(OVE):
        safety.validate_order(_o())  # no stop_price
    with pytest.raises(OVE):
        safety.validate_order(_o(stop_price="0"))
    with pytest.raises(OVE):
        safety.validate_order(_o(stop_price="abc"))


def test_stop_loss_rejects_stray_limit_price():
    with pytest.raises(OVE):
        safety.validate_order(_o(stop_price="105", limit_price="106"))


def test_stop_limit_requires_both_prices():
    with pytest.raises(OVE):
        safety.validate_order(_o(order_type="STOP_LOSS_LIMIT", stop_price="105"))
    with pytest.raises(OVE):
        safety.validate_order(_o(order_type="STOP_LOSS_LIMIT", limit_price="105"))
    ok = _o(order_type="STOP_LOSS_LIMIT", stop_price="105", limit_price="105.3")
    assert safety.validate_order(ok, last_price=100) is ok


def test_buy_stop_must_be_above_last():
    with pytest.raises(OVE):
        safety.validate_order(_o(stop_price="95"), last_price=100)  # wrong side -> instant fill


def test_sell_stop_must_be_below_last():
    with pytest.raises(OVE):
        safety.validate_order(_o(side="SELL", stop_price="110"), last_price=100)


def test_stop_deviation_guard():
    with pytest.raises(OVE):
        safety.validate_order(_o(stop_price="200"), last_price=100)  # 100% away


def test_valid_buy_stop_passes():
    o = _o(stop_price="105")
    assert safety.validate_order(o, last_price=100) is o


def test_valid_sell_stop_passes():
    o = _o(side="SELL", stop_price="95")
    assert safety.validate_order(o, last_price=100) is o


def test_side_guards_skipped_without_last_price():
    # No last_price -> wrong-side check can't run; structural validation still passes.
    assert safety.validate_order(_o(stop_price="95")) is not None


def test_should_submit_unchanged():
    assert safety.should_submit("prod", True) is True
    assert safety.should_submit("prod", False) is False
    assert safety.should_submit("test", False) is False
