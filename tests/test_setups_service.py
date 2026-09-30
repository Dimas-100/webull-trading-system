import pytest

from webull_api.market_data import MarketDataNotEntitledError
from webull_web import setups_service as svc


class _Found:
    """Stub of discovery.DiscoverResult (symbols/scanned/in_band are all the service reads)."""
    def __init__(self, symbols):
        self.symbols = list(symbols)
        self.scanned = len(symbols) + 5   # pool is bigger than the in-band slice
        self.in_band = len(symbols)
        self.no_price = []


def _raise(msg):
    raise AssertionError(msg)


def _setup(monkeypatch, *, bars_fn=None, lists=None, symbols=None, discovered=("AAA", "BBB")):
    monkeypatch.setattr(svc.discovery, "discover", lambda **k: _Found(discovered))
    monkeypatch.setattr(svc, "swing_book", lambda: None)   # broker not read in unit tests
    monkeypatch.setattr(svc.watchlist, "get_watchlists",
                        lambda: lists if lists is not None else [{"id": "1", "name": "My Watchlist"}])
    monkeypatch.setattr(svc.watchlist, "get_watchlist_symbols",
                        lambda wid: symbols if symbols is not None else [{"symbol": "AAA"}, {"symbol": "BBB"}])
    monkeypatch.setattr(svc.market_data, "get_snapshot", lambda s: [])
    monkeypatch.setattr(svc.market_data, "get_bars", bars_fn or (lambda sym, *a, **k: [{"c": 1}]))
    monkeypatch.setattr(svc, "_store_is_fresh", lambda s, d: False)   # unit tests never read the Tiingo store
    monkeypatch.setattr(svc, "to_ohlcv", lambda raw: raw if raw else [])
    monkeypatch.setattr(svc, "plan_swing",
                        lambda *a, **k: type("P", (), {"model_dump": lambda self: {"verdict": "SKIP"}})())
    monkeypatch.setattr(svc.news, "get_next_earnings_date", lambda s: None)
    monkeypatch.setattr(svc.setups, "scan_symbol",
                        lambda sym, daily, **k: {"symbol": sym, "tags": [{"kind": "oversold"}], "score": 2})


def test_default_scans_discovery_universe_never_watchlists(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(svc.watchlist, "get_watchlists", lambda: _raise("watchlists touched"))
    monkeypatch.setattr(svc.watchlist, "get_watchlist_symbols", lambda wid: _raise("watchlist read"))
    out = svc.scan_setups()
    assert out["source"]["kind"] == "discovery"
    assert out["source"]["in_band"] == 2 and out["source"]["universe"] == 7
    assert out["watchlist"] is None
    assert out["scanned"] == 2 and len(out["setups"]) == 2


def test_explicit_watchlist_mode_never_discovers(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(svc.discovery, "discover", lambda **k: _raise("discovery touched"))
    out = svc.scan_setups(name="My Watchlist")
    assert out["source"] == {"kind": "watchlist", "id": "1", "name": "My Watchlist"}
    assert out["watchlist"] == {"id": "1", "name": "My Watchlist"}
    assert out["scanned"] == 2


def test_per_symbol_error_isolated(monkeypatch):
    def bars(sym, *a, **k):
        if sym == "BBB":
            raise RuntimeError("boom")
        return [{"c": 1}]
    _setup(monkeypatch, bars_fn=bars)
    out = svc.scan_setups()
    assert out["scanned"] == 1
    assert any(e["symbol"] == "BBB" for e in out["errors"])


def test_entitlement_propagates(monkeypatch):
    def bars(sym, *a, **k):
        raise MarketDataNotEntitledError("nope")
    _setup(monkeypatch, bars_fn=bars)
    with pytest.raises(MarketDataNotEntitledError):
        svc.scan_setups()


def test_discovery_entitlement_propagates(monkeypatch):
    _setup(monkeypatch)
    def boom(**k):
        raise MarketDataNotEntitledError("nope")
    monkeypatch.setattr(svc.discovery, "discover", boom)
    with pytest.raises(MarketDataNotEntitledError):
        svc.scan_setups()


def test_discovery_outage_yields_honest_empty(monkeypatch):
    _setup(monkeypatch, discovered=())
    out = svc.scan_setups()
    assert out["setups"] == [] and out["scanned"] == 0
    assert out["source"]["kind"] == "discovery"


def test_named_watchlist_missing_empty(monkeypatch):
    _setup(monkeypatch, lists=[])
    out = svc.scan_setups(name="ghost")
    assert out["setups"] == [] and out["scanned"] == 0
    assert out["watchlist"] is None and out["source"]["kind"] == "watchlist"


def test_named_watchlist_missing_falls_back_to_default_list(monkeypatch):
    # The operator escape hatch keeps the old _resolve fallback: an unknown name with
    # existing lists resolves to "my watchlist" (or the first list) rather than erroring —
    # and still never touches discovery.
    _setup(monkeypatch)
    monkeypatch.setattr(svc.discovery, "discover", lambda **k: _raise("discovery touched"))
    out = svc.scan_setups(name="ghost")
    assert out["source"] == {"kind": "watchlist", "id": "1", "name": "My Watchlist"}
    assert out["scanned"] == 2


def test_discovery_band_tracks_the_live_swing_book_and_is_uncapped(monkeypatch):
    seen = {}

    def discover(**k):
        seen.update(k)
        return _Found(("AAA",))

    _setup(monkeypatch)
    monkeypatch.setattr(svc.discovery, "discover", discover)
    monkeypatch.setattr(svc, "swing_book", lambda: 846.0)     # $1,246 account minus the $400 day book
    out = svc.scan_setups()
    assert seen["limit"] is None                              # every in-band name is screened
    assert seen["max_price"] == 169.2                         # 20% of the swing book
    assert out["source"]["max_price"] == 169.2 and out["source"]["book"] == 846.0
    assert out["source"]["limit"] is None and out["source"]["min_price"] == 10.0


def test_discovery_band_falls_back_to_the_default_ceiling_without_a_book(monkeypatch):
    seen = {}

    def discover(**k):
        seen.update(k)
        return _Found(("AAA",))

    _setup(monkeypatch)
    monkeypatch.setattr(svc.discovery, "discover", discover)
    monkeypatch.setattr(svc, "swing_book", lambda: None)
    out = svc.scan_setups()
    assert seen["max_price"] == 100.0 and out["source"]["book"] is None


def test_swing_book_reads_the_individual_cash_account_minus_the_day_carve_out(monkeypatch):
    monkeypatch.setattr(svc.portfolio, "list_accounts", lambda: [
        {"account_id": "crypto", "account_class": "CRYPTO"},
        {"account_id": "cash", "account_class": "INDIVIDUAL_CASH"}])
    monkeypatch.setattr(svc.portfolio, "get_balance",
                        lambda aid: {"total_net_liquidation_value": "1245.93"} if aid == "cash" else _raise("wrong account"))
    assert svc.swing_book() == 1245.93   # DAY_CARVE_OUT = 0.0 (the $400 hand day book was retired 2026-09-21)
    monkeypatch.setattr(svc.portfolio, "get_balance", lambda aid: _raise("broker down"))
    assert svc.swing_book() is None


# --- store-first daily bars (2026-09-08: the swing screen reads the broad Tiingo store) ---
from datetime import date as _date  # noqa: E402


def _boom(*a, **k):
    raise KeyError("AAA")


def test_store_is_fresh_within_four_calendar_days(monkeypatch):
    monkeypatch.setattr(svc.tiingo_store, "last_date", lambda s: "2026-09-04")
    assert svc._store_is_fresh("AAA", _date(2026, 9, 8)) is True      # Friday's bar seen on Tuesday (weekend + holiday)
    assert svc._store_is_fresh("AAA", _date(2026, 9, 9)) is False     # five days old -> stale -> Webull
    monkeypatch.setattr(svc.tiingo_store, "last_date", lambda s: None)
    assert svc._store_is_fresh("AAA", _date(2026, 9, 8)) is False


def test_daily_bars_prefers_the_fresh_store_and_never_calls_webull(monkeypatch):
    rows = [{"time": "2026-09-03", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0},
            {"time": "2026-09-04", "open": 1.5, "high": 2.0, "low": 1.0, "close": 1.8, "volume": 12.0}]
    monkeypatch.setattr(svc, "_store_is_fresh", lambda s, d: True)
    monkeypatch.setattr(svc, "_store_get_bars", lambda s, ts, count: list(reversed(rows)))   # any order in
    monkeypatch.setattr(svc.market_data, "get_bars", lambda *a, **k: _raise("webull must not be called"))
    out = svc._daily_bars("AAA", _date(2026, 9, 8))
    assert [b["time"] for b in out] == ["2026-09-03", "2026-09-04"] and out[-1]["close"] == 1.8   # oldest-first out


def test_daily_bars_falls_back_to_webull_when_stale_or_missing(monkeypatch):
    calls = []
    monkeypatch.setattr(svc.market_data, "get_bars",
                        lambda sym, ts, count: calls.append((sym, ts, count)) or
                        [{"time": "2026-09-04", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}])
    monkeypatch.setattr(svc, "_store_is_fresh", lambda s, d: False)
    assert svc._daily_bars("AAA", _date(2026, 9, 8))[-1]["close"] == 1
    monkeypatch.setattr(svc, "_store_is_fresh", lambda s, d: True)
    monkeypatch.setattr(svc, "_store_get_bars", _boom)                 # fresh but unreadable -> Webull
    assert svc._daily_bars("AAA", _date(2026, 9, 8))[-1]["close"] == 1
    assert calls == [("AAA", "D", "250"), ("AAA", "D", "250")]


def test_scan_reads_symbols_and_spy_through_daily_bars(monkeypatch):
    seen = []
    _setup(monkeypatch)
    monkeypatch.setattr(svc, "_daily_bars", lambda sym, today, count="250": seen.append(sym) or [{"c": 1}])
    svc.scan_setups()
    assert "SPY" in seen and {"AAA", "BBB"} <= set(seen)
