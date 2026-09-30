from webull_api.journal.pairing import pair_fills
from webull_api.journal.schema import Fill, ThesisRecord


def _fill(fid, side, qty, price, t, symbol="AAPL", source="paper"):
    return Fill(id=fid, source=source, account_id="A", symbol=symbol, side=side,
                quantity=qty, price=price, filled_at_iso=t, order_type="MARKET", context=None)


def test_simple_round_trip_winner():
    fills = [_fill("b", "BUY", 10, 100.0, "2026-06-01T00:00:00+00:00"),
             _fill("s", "SELL", 10, 110.0, "2026-06-03T00:00:00+00:00")]
    closed, opens = pair_fills(fills)
    assert opens == []
    assert len(closed) == 1
    t = closed[0]
    assert t.quantity == 10 and t.entry_price == 100.0 and t.exit_price == 110.0
    assert t.pnl == 100.0 and t.win is True
    assert round(t.return_pct, 2) == 10.0
    assert round(t.holding_days, 0) == 2


def test_partial_sell_leaves_open_position():
    fills = [_fill("b", "BUY", 10, 100.0, "2026-06-01T00:00:00+00:00"),
             _fill("s", "SELL", 4, 120.0, "2026-06-02T00:00:00+00:00")]
    closed, opens = pair_fills(fills)
    assert len(closed) == 1 and closed[0].quantity == 4 and closed[0].pnl == 80.0
    assert len(opens) == 1 and opens[0].quantity == 6 and opens[0].avg_entry_price == 100.0


def test_multiple_lots_fifo():
    fills = [_fill("b1", "BUY", 5, 100.0, "2026-06-01T00:00:00+00:00"),
             _fill("b2", "BUY", 5, 110.0, "2026-06-02T00:00:00+00:00"),
             _fill("s", "SELL", 8, 120.0, "2026-06-03T00:00:00+00:00")]
    closed, opens = pair_fills(fills)
    # FIFO: 5 @100 fully, then 3 @110
    assert len(closed) == 2
    assert closed[0].entry_price == 100.0 and closed[0].quantity == 5
    assert closed[1].entry_price == 110.0 and closed[1].quantity == 3
    assert len(opens) == 1 and opens[0].quantity == 2 and opens[0].avg_entry_price == 110.0


def test_orphan_sell_excluded():
    fills = [_fill("s", "SELL", 5, 120.0, "2026-06-03T00:00:00+00:00")]
    closed, opens = pair_fills(fills)
    assert closed == [] and opens == []


def test_symbol_and_source_isolation():
    fills = [_fill("b1", "BUY", 1, 10.0, "2026-06-01T00:00:00+00:00", symbol="AAPL", source="real"),
             _fill("s1", "SELL", 1, 12.0, "2026-06-02T00:00:00+00:00", symbol="AAPL", source="real"),
             _fill("b2", "BUY", 1, 50.0, "2026-06-01T00:00:00+00:00", symbol="MSFT", source="paper"),
             _fill("s2", "SELL", 1, 45.0, "2026-06-02T00:00:00+00:00", symbol="MSFT", source="paper")]
    closed, opens = pair_fills(fills)
    assert len(closed) == 2
    aapl = [t for t in closed if t.symbol == "AAPL"][0]
    msft = [t for t in closed if t.symbol == "MSFT"][0]
    assert aapl.source == "real" and aapl.win is True
    assert msft.source == "paper" and msft.win is False


def test_sell_exact_total_leaves_no_open():
    fills = [_fill("b1", "BUY", 5, 100.0, "2026-06-01T00:00:00+00:00"),
             _fill("b2", "BUY", 5, 110.0, "2026-06-02T00:00:00+00:00"),
             _fill("s", "SELL", 10, 120.0, "2026-06-03T00:00:00+00:00")]
    closed, opens = pair_fills(fills)
    assert len(closed) == 2 and opens == []


def test_mixed_timezone_sorts_by_real_instant():
    # Lexicographically "09:00-05:00" < "10:00+00:00", but the -05:00 buy is 14:00 UTC (LATER).
    # FIFO by true instant must consume the 10:00Z buy (price 100) first.
    fills = [
        _fill("b_late", "BUY", 1, 200.0, "2026-06-01T09:00:00-05:00"),   # 14:00 UTC (later)
        _fill("b_early", "BUY", 1, 100.0, "2026-06-01T10:00:00+00:00"),  # 10:00 UTC (earlier)
        _fill("s", "SELL", 1, 150.0, "2026-06-02T00:00:00+00:00"),
    ]
    closed, opens = pair_fills(fills)
    assert len(closed) == 1
    assert closed[0].entry_price == 100.0          # earliest TRUE instant consumed first
    assert len(opens) == 1 and opens[0].avg_entry_price == 200.0


# ---------------------------------------------------------------------------
# Thesis propagation through pairing
# ---------------------------------------------------------------------------

def _fill_with_thesis(fid, side, qty, price, t, thesis=None, symbol="AAPL", source="paper"):
    return Fill(id=fid, source=source, account_id="A", symbol=symbol, side=side,
                quantity=qty, price=price, filled_at_iso=t, order_type="MARKET",
                context=None, thesis=thesis)


def test_buy_thesis_carried_to_closed_trade():
    """A BUY with a ThesisRecord: the resulting ClosedTrade.thesis equals the BUY's thesis."""
    thesis = ThesisRecord(confidence=4, setup="breakout", expected_move_pct=5.0,
                          horizon="days", invalidation="close below support")
    fills = [
        _fill_with_thesis("b", "BUY", 10, 100.0, "2026-06-01T00:00:00+00:00", thesis=thesis),
        _fill_with_thesis("s", "SELL", 10, 115.0, "2026-06-03T00:00:00+00:00"),
    ]
    closed, opens = pair_fills(fills)
    assert opens == []
    assert len(closed) == 1
    ct = closed[0]
    assert ct.thesis == thesis
    # mfe/mae are NOT filled by pairing — that's for higher layers
    assert ct.mfe is None
    assert ct.mae is None


def test_buy_without_thesis_pairs_to_closed_trade_with_no_thesis():
    """A BUY with no thesis: the resulting ClosedTrade.thesis is None (existing behavior)."""
    fills = [
        _fill_with_thesis("b", "BUY", 5, 200.0, "2026-06-01T00:00:00+00:00", thesis=None),
        _fill_with_thesis("s", "SELL", 5, 220.0, "2026-06-02T00:00:00+00:00"),
    ]
    closed, opens = pair_fills(fills)
    assert opens == []
    assert len(closed) == 1
    assert closed[0].thesis is None
    assert closed[0].mfe is None
    assert closed[0].mae is None


# ---------------------------------------------------------------------------
# Account isolation (real fills from different Webull accounts must never pair)
# ---------------------------------------------------------------------------

def _fill_in_account(fid, side, qty, price, t, account_id, symbol="AAPL", source="real"):
    return Fill(id=fid, source=source, account_id=account_id, symbol=symbol, side=side,
                quantity=qty, price=price, filled_at_iso=t, order_type="MARKET", context=None)


def test_fills_in_different_accounts_do_not_pair():
    fills = [
        _fill_in_account("b", "BUY", 5, 100.0, "2026-06-01T00:00:00+00:00", "ACCT_A"),
        _fill_in_account("s", "SELL", 5, 120.0, "2026-06-02T00:00:00+00:00", "ACCT_B"),
    ]
    closed, opens = pair_fills(fills)
    assert closed == []                                     # no fabricated cross-account round-trip
    assert len(opens) == 1 and opens[0].quantity == 5       # A's BUY stays open; B's SELL orphaned


def test_fills_in_same_account_still_pair():
    fills = [
        _fill_in_account("b", "BUY", 5, 100.0, "2026-06-01T00:00:00+00:00", "ACCT_A"),
        _fill_in_account("s", "SELL", 5, 120.0, "2026-06-02T00:00:00+00:00", "ACCT_A"),
    ]
    closed, opens = pair_fills(fills)
    assert len(closed) == 1 and closed[0].pnl == 100.0 and opens == []
