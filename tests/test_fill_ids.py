"""entry_fill_id/exit_fill_id threading: the exact-join key from a ClosedTrade back to the
action log (action.ref == Fill.id). Additive — legacy rows without the fields still load."""
from webull_api.journal import analytics
from webull_api.journal.pairing import pair_fills
from webull_api.journal.schema import ClosedTrade, Fill, OptionTrade

# NOTE: order_type is a required Fill field (per tests/test_fill_attribution.py's helper) —
# the brief's minimal sketch omitted it, so it's added here to satisfy pydantic validation.


def _fill(fid, side, qty=10.0, price=100.0, ts="2026-07-01T10:00:00-04:00"):
    return Fill(id=fid, source="paper", account_id="default", symbol="AAPL", side=side,
                quantity=qty, price=price, filled_at_iso=ts, order_type="MARKET")


def test_pair_fills_threads_entry_and_exit_ids():
    closed, _ = pair_fills([_fill("b1", "BUY"),
                            _fill("s1", "SELL", ts="2026-07-03T10:00:00-04:00")])
    assert len(closed) == 1
    assert closed[0].entry_fill_id == "b1" and closed[0].exit_fill_id == "s1"


def test_partial_exits_share_entry_id_distinct_exit_ids():
    closed, _ = pair_fills([
        _fill("b1", "BUY", qty=10.0),
        _fill("s1", "SELL", qty=4.0, ts="2026-07-02T10:00:00-04:00"),
        _fill("s2", "SELL", qty=6.0, ts="2026-07-03T10:00:00-04:00")])
    assert [c.entry_fill_id for c in closed] == ["b1", "b1"]
    assert sorted(c.exit_fill_id for c in closed) == ["s1", "s2"]


def test_option_trade_to_closed_sets_exit_fill_id():
    t = OptionTrade(id="close9", source="paper_option", underlying="GOOG",
                    strategy="VERTICAL", legs_desc="GOOG -350C / +360C 2026-08-07",
                    quantity=1, open_net_price=5.0, close_value=0.26,
                    opened_at_iso="2026-07-13T17:30:00-04:00",
                    closed_at_iso="2026-07-23T17:30:00-04:00",
                    pnl=-474.0, win=False, return_pct=-94.8, reason="closed")
    c = analytics.option_trade_to_closed(t)
    assert c.exit_fill_id == "close9" and c.entry_fill_id is None


def test_legacy_closed_trade_rows_still_validate():
    c = ClosedTrade(symbol="AAPL", source="paper", quantity=1.0, entry_price=1.0,
                    exit_price=2.0, entry_at_iso="2026-07-01T10:00:00-04:00",
                    exit_at_iso="2026-07-02T10:00:00-04:00", holding_days=1.0,
                    pnl=1.0, return_pct=100.0, win=True)
    assert c.entry_fill_id is None and c.exit_fill_id is None
