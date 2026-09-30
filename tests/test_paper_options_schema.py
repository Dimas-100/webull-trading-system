from webull_api.paper.options_schema import (
    OptionLeg, OptionPositionUnit, OptionPaperOrder, OptionsPaperAccount, OptionsPaperError,
)
from webull_api.safety import OrderValidationError


def _leg(side="BUY"):
    return OptionLeg(occ="AAPL260717C00300000", underlying="AAPL", option_type="CALL",
                     strike=300.0, expiration="2026-07-17", side=side)


def test_error_subclasses_order_validation():
    assert issubclass(OptionsPaperError, OrderValidationError)


def test_account_round_trips():
    acct = OptionsPaperAccount(starting_cash=10000.0, cash=10000.0,
                               created_at="t0", updated_at="t0")
    unit = OptionPositionUnit(unit_id="u1", strategy="SINGLE", legs=[_leg()], quantity=1,
                              avg_net_price=2.5, collateral=0.0, opened_at="t1")
    acct.positions.append(unit)
    dumped = acct.model_dump()
    restored = OptionsPaperAccount.model_validate(dumped)
    assert restored.account_id == "options"
    assert restored.positions[0].legs[0].occ == "AAPL260717C00300000"
    assert restored.reserved_collateral == 0.0


def test_order_defaults():
    o = OptionPaperOrder(paper_order_id="o1", strategy="SINGLE", legs=[_leg()], quantity=1,
                         intent="OPEN", order_type="MARKET", created_at="t", placed_et_date="2026-07-01")
    assert o.status == "pending" and o.time_in_force == "DAY" and o.close_unit_id is None
