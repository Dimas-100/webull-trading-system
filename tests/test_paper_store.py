from webull_web import paper_store
from webull_api.paper import engine


def test_load_missing_returns_none(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    assert paper_store.load() is None


def test_save_then_load_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("PAPER_DIR", str(tmp_path))
    acct = engine.open_account(5000.0, "t0")
    paper_store.save(acct.model_dump())
    loaded = paper_store.load()
    assert loaded["cash"] == 5000.0 and loaded["account_id"] == "default"
    # the loaded dict re-validates into a PaperAccount
    from webull_api.paper.schema import PaperAccount
    assert PaperAccount.model_validate(loaded).starting_cash == 5000.0


# paper_service.last_prices (the store's price side, shared by the paper runners and the paper MCP): ported
# 2026-09-28 from the archived /api/paper route tests, which were its only coverage.
def test_last_prices_maps_the_snapshot_and_degrades_on_an_outage(monkeypatch):
    from webull_web import paper_service
    monkeypatch.setattr(paper_service.market_data, "get_snapshot",
                        lambda symbols, *a, **k: [{"symbol": s, "price": 100.0} for s in symbols.split(",")])
    assert paper_service.last_prices(["aapl"]) == {"AAPL": 100.0}

    def boom(*a, **k):
        raise RuntimeError("upstream down")
    monkeypatch.setattr(paper_service.market_data, "get_snapshot", boom)
    assert paper_service.last_prices(["AAPL"]) == {}       # degrades: orders just don't advance, marks null


def test_last_prices_surfaces_a_missing_entitlement(monkeypatch):
    import pytest
    from webull_api.market_data import MarketDataNotEntitledError
    from webull_web import paper_service

    def boom(*a, **k):
        raise MarketDataNotEntitledError("subscribe")
    monkeypatch.setattr(paper_service.market_data, "get_snapshot", boom)
    with pytest.raises(MarketDataNotEntitledError):
        paper_service.last_prices(["AAPL"])


def test_account_symbols_is_held_positions_plus_open_orders():
    """paper_service.account_symbols -- the price-fetch set for netliq_snapshot_service and the paper MCP:
    every held symbol plus every OPEN order's symbol, each once; history (filled/cancelled) never counts.
    Its only coverage was the archived /api/paper route tests (2026-09-28)."""
    from webull_api.paper.schema import PaperOrder, PaperPosition
    from webull_web import paper_service

    def order(sym, side, status="pending"):
        return PaperOrder(paper_order_id=f"o-{sym}-{status}", symbol=sym, side=side, order_type="LIMIT",
                          quantity=1, limit_price=50.0, status=status, created_at="t0",
                          placed_et_date="2026-09-28")

    acct = engine.open_account(5000.0, "t0")
    assert paper_service.account_symbols(acct) == set()
    acct.positions["AAPL"] = PaperPosition(symbol="AAPL", quantity=2, avg_cost=100.0)
    acct.open_orders += [order("AAPL", "SELL"), order("MSFT", "BUY")]     # AAPL: held AND an open order
    acct.history.append(order("TSLA", "BUY", status="filled"))            # closed -> not priced
    assert paper_service.account_symbols(acct) == {"AAPL", "MSFT"}
