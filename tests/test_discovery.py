"""Tests for the candidate-discovery engine (webull_api/discovery.py).

Pure logic — get_snapshot is injected, no network. Verifies the band filter, dedupe/ordering, movers
injection, the cap, entitlement propagation, no-price handling, and the all-or-nothing batch fallback.
"""
from __future__ import annotations

import pytest

from webull_api import discovery
from webull_api.market_data import MarketDataNotEntitledError


def _snap(prices: dict[str, float]):
    """Build a fake get_snapshot returning the SDK's list-of-rows shape for the requested symbols."""
    def get_snapshot(symbols, category="US_STOCK"):
        syms = symbols if isinstance(symbols, list) else [s.strip() for s in str(symbols).split(",")]
        return [{"symbol": s, "price": prices[s]} for s in syms if s in prices]
    return get_snapshot


def test_filters_to_price_band():
    got = discovery.discover(
        ["AAA", "BBB", "CCC", "DDD"],
        min_price=10, max_price=100,
        get_snapshot=_snap({"AAA": 5.0, "BBB": 25.0, "CCC": 250.0, "DDD": 99.99}),
    )
    assert got.symbols == ["BBB", "DDD"]
    assert got.in_band == 2
    assert got.scanned == 4


def test_band_is_inclusive_on_both_ends():
    got = discovery.discover(
        ["LO", "HI"], min_price=10, max_price=100,
        get_snapshot=_snap({"LO": 10.0, "HI": 100.0}),
    )
    assert got.symbols == ["LO", "HI"]


def test_dedupes_and_uppercases_preserving_order():
    got = discovery.discover(
        ["bbb", "AAA", "BBB", "aaa"], min_price=1, max_price=1000,
        get_snapshot=_snap({"BBB": 20.0, "AAA": 30.0}),
    )
    assert got.symbols == ["BBB", "AAA"]
    assert got.scanned == 2


def test_extra_symbols_injected_after_core():
    got = discovery.discover(
        ["CORE1", "CORE2"], extra_symbols=["MOVER", "CORE1"],
        min_price=1, max_price=1000,
        get_snapshot=_snap({"CORE1": 20.0, "CORE2": 30.0, "MOVER": 40.0}),
    )
    assert got.symbols == ["CORE1", "CORE2", "MOVER"]


def test_limit_caps_core_first():
    prices = {f"S{i}": 50.0 for i in range(10)}
    got = discovery.discover(list(prices), min_price=1, max_price=1000, limit=3,
                             get_snapshot=_snap(prices))
    assert got.symbols == ["S0", "S1", "S2"]
    assert got.in_band == 10


def test_no_price_recorded_not_dropped_silently():
    got = discovery.discover(
        ["HASP", "NOPX"], min_price=1, max_price=1000,
        get_snapshot=_snap({"HASP": 50.0}),
    )
    assert got.symbols == ["HASP"]
    assert got.no_price == ["NOPX"]


def test_entitlement_error_propagates():
    def boom(symbols, category="US_STOCK"):
        raise MarketDataNotEntitledError("nope")
    with pytest.raises(MarketDataNotEntitledError):
        discovery.discover(["AAA"], get_snapshot=boom)


def test_other_snapshot_error_degrades_to_no_price():
    def boom(symbols, category="US_STOCK"):
        raise RuntimeError("transient")
    got = discovery.discover(["AAA", "BBB"], get_snapshot=boom)
    assert got.symbols == []
    assert set(got.no_price) == {"AAA", "BBB"}


def test_handles_data_envelope_shape():
    def get_snapshot(symbols, category="US_STOCK"):
        return {"data": [{"symbol": s, "price": 42.0} for s in symbols]}
    got = discovery.discover(["AAA", "BBB"], min_price=1, max_price=1000, get_snapshot=get_snapshot)
    assert got.symbols == ["AAA", "BBB"]


def test_empty_universe_is_safe():
    got = discovery.discover([], get_snapshot=_snap({}))
    assert got.symbols == [] and got.scanned == 0


def test_bad_ticker_isolates_not_drops_batch():
    good = {f"G{i}": 50.0 for i in range(20)}

    def get_snapshot(symbols, category="US_STOCK"):
        syms = symbols if isinstance(symbols, list) else [s.strip() for s in str(symbols).split(",")]
        if "BAD" in syms:
            raise RuntimeError("417 invalid symbol: BAD")
        return [{"symbol": s, "price": good[s]} for s in syms if s in good]

    universe = list(good) + ["BAD"]
    got = discovery.discover(universe, min_price=1, max_price=1000, limit=100,
                             get_snapshot=get_snapshot)
    assert set(got.symbols) == set(good)
    assert got.no_price == ["BAD"]


def test_entitlement_error_still_propagates_through_split():
    def boom(symbols, category="US_STOCK"):
        raise MarketDataNotEntitledError("nope")
    with pytest.raises(MarketDataNotEntitledError):
        discovery.discover(["AAA", "BBB", "CCC"], get_snapshot=boom)


def test_curated_universe_is_clean():
    u = discovery.CURATED_UNIVERSE
    assert len(u) > 100
    assert all(s == s.upper() and s.strip() == s for s in u)
    assert len(set(u)) == len(u)
    # 2026-07-12 prune: dead tickers 417 their snapshot chunk and cost split retries.
    for dead in ("DFS", "MRO", "X", "PARA", "GPS", "K", "WBA", "HOLX"):
        assert dead not in u
    assert "GAP" in u and "PSKY" in u  # the renamed successors


def test_limit_none_screens_every_in_band_name():
    prices = {f"S{i}": 50.0 for i in range(60)}
    got = discovery.discover(list(prices), min_price=1, max_price=1000, limit=None,
                             get_snapshot=_snap(prices))
    assert len(got.symbols) == 60 and got.in_band == 60


def test_band_ceiling_tracks_the_book_with_the_default_as_a_floor():
    assert discovery.band_ceiling(None) == 100.0
    assert discovery.band_ceiling(0) == 100.0
    assert discovery.band_ceiling(400) == 100.0          # 20% of $400 is $80 — the floor wins
    assert discovery.band_ceiling(846) == 169.2
    assert discovery.band_ceiling(3000) == 600.0
