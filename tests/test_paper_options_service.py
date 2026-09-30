# tests/test_paper_options_service.py
from webull_api import options
from webull_web import paper_options_service as svc


def test_marks_for_parses_snapshot(monkeypatch):
    monkeypatch.setattr(options, "get_option_snapshot",
                        lambda occs: [{"symbol": "AAPL260717C00300000", "bidPrice": "5.0",
                                       "askPrice": "5.4", "price": "5.2"}])
    m = svc.marks_for(["AAPL260717C00300000"])
    row = m["AAPL260717C00300000"]
    assert row["bid"] == 5.0 and row["ask"] == 5.4 and row["mid"] == 5.2


def test_marks_for_degrades_on_error(monkeypatch):
    def boom(occs):
        raise RuntimeError("417 bad symbol")
    monkeypatch.setattr(options, "get_option_snapshot", boom)
    assert svc.marks_for(["X"]) == {}


def test_marks_for_propagates_entitlement(monkeypatch):
    from webull_api.market_data import MarketDataNotEntitledError
    def boom(occs):
        raise MarketDataNotEntitledError("nope")
    monkeypatch.setattr(options, "get_option_snapshot", boom)
    try:
        svc.marks_for(["X"])
        assert False, "should propagate"
    except MarketDataNotEntitledError:
        pass


def test_marks_for_mid_fallback_to_last(monkeypatch):
    """When bid/ask missing, mid falls back to last."""
    monkeypatch.setattr(options, "get_option_snapshot",
                        lambda occs: [{"symbol": "SPY260717C00500000", "price": "3.5"}])
    m = svc.marks_for(["SPY260717C00500000"])
    row = m["SPY260717C00500000"]
    assert row["bid"] is None and row["ask"] is None and row["mid"] == 3.5


def test_marks_for_empty_list():
    assert svc.marks_for([]) == {}


def test_spots_for_propagates_entitlement(monkeypatch):
    from webull_api import market_data
    from webull_api.market_data import MarketDataNotEntitledError
    def boom(sym):
        raise MarketDataNotEntitledError("nope")
    monkeypatch.setattr(market_data, "spot_price", boom)
    try:
        svc.spots_for({"AAPL"})
        assert False, "should propagate"
    except MarketDataNotEntitledError:
        pass


def test_spots_for_degrades_on_error(monkeypatch):
    from webull_api import market_data
    def boom(sym):
        raise RuntimeError("timeout")
    monkeypatch.setattr(market_data, "spot_price", boom)
    assert svc.spots_for({"AAPL"}) == {}


def test_spots_for_returns_prices(monkeypatch):
    from webull_api import market_data
    monkeypatch.setattr(market_data, "spot_price", lambda sym: 150.0 if sym == "AAPL" else 500.0)
    result = svc.spots_for({"AAPL", "SPY"})
    assert result["AAPL"] == 150.0
    assert result["SPY"] == 500.0


def test_account_occs_empty():
    from webull_api.paper.options_schema import OptionsPaperAccount
    acct = OptionsPaperAccount(starting_cash=100000.0, cash=100000.0, created_at="2026-01-01", updated_at="2026-01-01")
    assert svc.account_occs(acct) == []


def test_account_occs_from_positions():
    from webull_api.paper.options_schema import OptionsPaperAccount, OptionPositionUnit, OptionLeg
    leg = OptionLeg(occ="AAPL260717C00300000", underlying="AAPL", option_type="CALL",
                    strike=300.0, expiration="2026-07-17", side="BUY")
    unit = OptionPositionUnit(unit_id="u1", strategy="SINGLE", legs=[leg],
                              quantity=1, avg_net_price=5.0, collateral=0.0, opened_at="2026-01-01")
    acct = OptionsPaperAccount(starting_cash=100000.0, cash=99500.0, positions=[unit],
                               created_at="2026-01-01", updated_at="2026-01-01")
    assert svc.account_occs(acct) == ["AAPL260717C00300000"]


def test_account_underlyings_empty():
    from webull_api.paper.options_schema import OptionsPaperAccount
    acct = OptionsPaperAccount(starting_cash=100000.0, cash=100000.0, created_at="2026-01-01", updated_at="2026-01-01")
    assert svc.account_underlyings(acct) == set()


def test_account_underlyings_from_positions():
    from webull_api.paper.options_schema import OptionsPaperAccount, OptionPositionUnit, OptionLeg
    leg = OptionLeg(occ="AAPL260717C00300000", underlying="AAPL", option_type="CALL",
                    strike=300.0, expiration="2026-07-17", side="BUY")
    unit = OptionPositionUnit(unit_id="u1", strategy="SINGLE", legs=[leg],
                              quantity=1, avg_net_price=5.0, collateral=0.0, opened_at="2026-01-01")
    acct = OptionsPaperAccount(starting_cash=100000.0, cash=99500.0, positions=[unit],
                               created_at="2026-01-01", updated_at="2026-01-01")
    assert svc.account_underlyings(acct) == {"AAPL"}


def test_load_account_creates_new_when_no_store(monkeypatch):
    from webull_web import paper_options_store
    monkeypatch.setattr(paper_options_store, "load", lambda: None)
    acct = svc.load_account()
    assert acct.cash == svc.DEFAULT_CASH
    assert acct.starting_cash == svc.DEFAULT_CASH


def test_load_account_restores_from_store(monkeypatch):
    from webull_web import paper_options_store
    data = {
        "account_id": "options", "starting_cash": 50000.0, "cash": 48000.0,
        "reserved_collateral": 0.0, "positions": [], "open_orders": [], "history": [],
        "realized_pnl": 0.0, "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00"
    }
    monkeypatch.setattr(paper_options_store, "load", lambda: data)
    acct = svc.load_account()
    assert acct.cash == 48000.0


def test_save_account_calls_trim_and_store(monkeypatch):
    from webull_api.paper.options_schema import OptionsPaperAccount
    from webull_api.paper import options_engine as eng
    from webull_web import paper_options_store
    trimmed = []
    saved = []
    monkeypatch.setattr(eng, "trim_history", lambda acct, cap: trimmed.append(cap))
    monkeypatch.setattr(paper_options_store, "save", lambda d: saved.append(d))
    acct = OptionsPaperAccount(starting_cash=100000.0, cash=100000.0, created_at="2026-01-01", updated_at="2026-01-01")
    svc.save_account(acct)
    assert trimmed == [svc.HISTORY_CAP]
    assert len(saved) == 1


def test_now_et_returns_iso_and_ymd():
    iso, ymd = svc.now_et()
    assert "T" in iso
    assert len(ymd) == 10 and ymd[4] == "-" and ymd[7] == "-"


def test_lock_is_rlock():
    import threading
    assert isinstance(svc.LOCK, type(threading.RLock()))


# ── settle_closes_for (best-effort expiration-day closes from daily bars) ──────

def _expired_unit_acct():
    from webull_api.paper.options_schema import OptionsPaperAccount, OptionPositionUnit, OptionLeg
    leg = OptionLeg(occ="AAPL260717C00300000", underlying="AAPL", option_type="CALL",
                    strike=300.0, expiration="2026-07-17", side="BUY")
    unit = OptionPositionUnit(unit_id="u1", strategy="SINGLE", legs=[leg], quantity=1,
                              avg_net_price=5.0, collateral=0.0, opened_at="t0")
    return OptionsPaperAccount(starting_cash=1000.0, cash=500.0, positions=[unit],
                               created_at="t0", updated_at="t0")


def test_settle_closes_for_reads_exp_day_close(monkeypatch):
    from webull_api import market_data
    monkeypatch.setattr(market_data, "get_bars",
                        lambda sym, **kw: [{"time": "2026-07-16", "open": 1, "high": 1, "low": 1,
                                            "close": 318.0, "volume": 1},
                                           {"time": "2026-07-17", "open": 1, "high": 1, "low": 1,
                                            "close": 321.5, "volume": 1}])
    out = svc.settle_closes_for(_expired_unit_acct(), "2026-07-18")
    assert out == {("AAPL", "2026-07-17"): 321.5}


def test_settle_closes_for_degrades_on_error(monkeypatch):
    from webull_api import market_data
    def boom(sym, **kw):
        raise RuntimeError("bars unavailable")
    monkeypatch.setattr(market_data, "get_bars", boom)
    assert svc.settle_closes_for(_expired_unit_acct(), "2026-07-18") == {}


def test_settle_closes_for_skips_unexpired(monkeypatch):
    from webull_api import market_data
    called = []
    monkeypatch.setattr(market_data, "get_bars", lambda sym, **kw: called.append(sym) or [])
    assert svc.settle_closes_for(_expired_unit_acct(), "2026-07-17") == {}  # exp == today: not expired
    assert called == []                                                     # no needless fetch


def test_spot_and_settle_fetches_use_the_universe_spelling_not_the_occ_root(monkeypatch):
    """Live-verified 2026-08-15: market data wants 'BRK B' and 417s on 'BRKB', while
    OptionLeg.underlying stores the OCC root. Fetches must translate root -> universe form
    or a BRK B unit can never price its spot or settle-close."""
    from webull_web import paper_options_service as svc

    fetched = []

    def fake_spot(sym):
        fetched.append(("spot", sym))
        if sym == "BRKB":
            raise RuntimeError("HTTP Status: 417, Code: INVALID_SYMBOL")
        return 504.03

    monkeypatch.setattr(svc.market_data, "spot_price", fake_spot)
    out = svc.spots_for(["BRKB", "AAPL"])
    assert ("spot", "BRK B") in fetched, "root form must be translated before the fetch"
    assert ("spot", "BRKB") not in fetched
    assert out["BRKB"] == 504.03, "the result stays keyed by the caller's (root) key"
    assert out["AAPL"] == 504.03
