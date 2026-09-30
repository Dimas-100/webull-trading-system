"""paper_settle_service: settle-at-open step every equity runner calls first."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.paper import engine as paper_engine  # noqa: E402
from webull_web import paper_settle_service  # noqa: E402


def _acct_with_queued(day="2026-07-27"):
    acct = paper_engine.open_account(100000.0, f"{day}T17:30:00")
    acct, order = paper_engine.place_order(
        acct, symbol="AAPL", side="BUY", order_type="MARKET", quantity=10,
        limit_price=None, time_in_force="DAY", now_iso=f"{day}T17:30:00", today_et=day,
        last_price=200.0, fill_policy="next_open")
    return acct, order


def test_settle_book_fills_and_saves(monkeypatch):
    acct, order = _acct_with_queued()
    saved = {}
    monkeypatch.setattr(paper_settle_service.paper_service, "now_et",
                        lambda: ("2026-07-28T17:30:00", "2026-07-28"))
    monkeypatch.setattr(paper_settle_service.market_data, "get_bars",
                        lambda sym, tf, count: [
                            {"time": "2026-07-28", "open": "204.5", "high": "206", "low": "203",
                             "close": "205", "volume": "1"},
                            {"time": "2026-07-27", "open": "199.0", "high": "201", "low": "198",
                             "close": "200", "volume": "1"}])  # newest-first, like the SDK
    res = paper_settle_service.settle_book(load=lambda: acct,
                                           save=lambda a: saved.setdefault("acct", a))
    assert [o.paper_order_id for o in res["settled"]] == [order.paper_order_id]
    assert res["settled"][0].fill_price == 204.5
    assert res["rejected"] == [] and res["resting"] == [] and res["stale"] == []
    assert "acct" in saved  # mutation persisted


def test_settle_book_no_queue_is_noop_no_save(monkeypatch):
    acct = paper_engine.open_account(100000.0, "t0")
    called = {"bars": 0, "save": 0}
    monkeypatch.setattr(paper_settle_service.market_data, "get_bars",
                        lambda *a, **k: called.__setitem__("bars", called["bars"] + 1))
    res = paper_settle_service.settle_book(load=lambda: acct,
                                           save=lambda a: called.__setitem__("save", 1))
    assert res == {"settled": [], "rejected": [], "resting": [], "stale": [], "errors": []}
    assert called["bars"] == 0 and called["save"] == 0


def test_settle_book_bar_fetch_failure_degrades(monkeypatch):
    acct, _ = _acct_with_queued()
    monkeypatch.setattr(paper_settle_service.paper_service, "now_et",
                        lambda: ("2026-07-28T17:30:00", "2026-07-28"))

    def boom(sym, tf, count):
        raise RuntimeError("network down")

    monkeypatch.setattr(paper_settle_service.market_data, "get_bars", boom)
    res = paper_settle_service.settle_book(load=lambda: acct, save=lambda a: None)
    assert res["settled"] == [] and len(res["resting"]) == 1
    assert res["errors"] == ["AAPL: bar fetch failed"]
    assert len(res["stale"]) == 1  # placed 07-27, still resting on 07-28 -> stale


def test_settle_book_same_evening_not_stale(monkeypatch):
    acct, _ = _acct_with_queued(day="2026-07-27")
    monkeypatch.setattr(paper_settle_service.paper_service, "now_et",
                        lambda: ("2026-07-27T17:31:00", "2026-07-27"))
    monkeypatch.setattr(paper_settle_service.market_data, "get_bars",
                        lambda sym, tf, count: [
                            {"time": "2026-07-27", "open": "199.0", "high": "201", "low": "198",
                             "close": "200", "volume": "1"}])
    res = paper_settle_service.settle_book(load=lambda: acct, save=lambda a: None)
    assert len(res["resting"]) == 1 and res["stale"] == []
