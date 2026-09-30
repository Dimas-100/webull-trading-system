"""Loop seam #2 (slice 2a): strategy provenance threads from the fill atom -> ClosedTrade, so a
proven strategy's LIVE paper trades can be attributed. Additive fields; old data still parses."""
from webull_api.journal import pairing
from webull_api.journal.schema import Fill


def _fill(side, qty, price, at, *, sid=None, tid=None):
    return Fill(id=f"{side}-{at}", source="paper", account_id="proven", symbol="AAA", side=side,
                quantity=qty, price=price, filled_at_iso=at, order_type="MARKET",
                strategy_id=sid, trial_id=tid)


def test_pair_fills_threads_attribution_from_the_buy_lot():
    fills = [
        _fill("BUY", 10, 10.0, "2026-07-01T10:00:00-04:00", sid="S1", tid="T1"),
        _fill("SELL", 10, 12.0, "2026-07-08T10:00:00-04:00"),  # the close inherits the matched lot's tag
    ]
    closed, _ = pairing.pair_fills(fills)
    assert len(closed) == 1
    assert closed[0].strategy_id == "S1" and closed[0].trial_id == "T1"


def test_pair_fills_attribution_is_none_when_unset():
    fills = [
        _fill("BUY", 10, 10.0, "2026-07-01T10:00:00-04:00"),
        _fill("SELL", 10, 12.0, "2026-07-08T10:00:00-04:00"),
    ]
    closed, _ = pairing.pair_fills(fills)
    assert closed[0].strategy_id is None and closed[0].trial_id is None


def test_fill_parses_without_the_new_fields():
    f = Fill.model_validate({"id": "x", "source": "paper", "account_id": "a", "symbol": "AAA",
                             "side": "BUY", "quantity": 1, "price": 1.0, "filled_at_iso": "t",
                             "order_type": "MARKET"})
    assert f.strategy_id is None and f.trial_id is None


def _f(fid, sym, side, price, at, *, sid=None, tid=None, acct="default"):
    return Fill(id=fid, source="paper", account_id=acct, symbol=sym, side=side, quantity=1,
                price=price, filled_at_iso=at, order_type="MARKET", strategy_id=sid, trial_id=tid)


def test_fills_from_paper_account_copies_provenance():
    from webull_api.journal import normalize
    acct = {"account_id": "proven", "starting_cash": 1000.0, "cash": 990.0, "positions": {},
            "open_orders": [], "created_at": "t", "updated_at": "t",
            "history": [{"paper_order_id": "o1", "symbol": "AAA", "side": "BUY", "order_type": "MARKET",
                         "quantity": 1, "status": "filled", "created_at": "t", "placed_et_date": "d",
                         "filled_at": "2026-07-01", "fill_price": 10.0,
                         "strategy_id": "S1", "trial_id": "T1"}]}
    fills = normalize.fills_from_paper_account(acct)  # context_fn=None -> pure, no network
    assert fills[0].strategy_id == "S1" and fills[0].trial_id == "T1"


def test_proof_bar_excludes_proven_strategy_trades(monkeypatch):
    # The discipline proof bar (decisions-toward-30) must ignore proven-strategy auto-trades.
    # Under the code-enforced proof-bar-read-scope (2026-08-16) the bar row must be a POST-FIX
    # rsi2-attributed round-trip; the proven BBB round-trip lands in context, never the bar.
    from webull_web import north_star_service as ns
    fills = [
        _f("d1", "AAA", "BUY", 10.0, "2026-08-03T17:30:00-04:00"),           # rsi2 round-trip
        _f("d2", "AAA", "SELL", 12.0, "2026-08-06T17:30:00-04:00"),
        _f("p1", "BBB", "BUY", 10.0, "2026-08-03T17:30:00-04:00", sid="S1", tid="T1",
           acct="proven"),                                                    # proven auto-trade
        _f("p2", "BBB", "SELL", 8.0, "2026-08-06T17:30:00-04:00", acct="proven"),
    ]
    monkeypatch.setattr(ns.journal_store, "load_fills", lambda: fills)
    monkeypatch.setattr(ns.journal_store, "load_option_trades", lambda: [])
    monkeypatch.setattr(ns.action_log, "load",
                        lambda: [{"id": "a1", "ref": "d1", "source": "runner:rsi2",
                                  "kind": "trade"}])
    monkeypatch.setattr(ns.paper_store, "load", lambda: {"realized_pnl": 0.0, "positions": {}})
    monkeypatch.setattr(ns.paper_options_store, "load", lambda: {"realized_pnl": 0.0, "positions": []})
    p = ns._paper_inputs()
    assert p["decisions"] == 1                   # only AAA; the proven BBB round-trip is excluded
    assert p["context"]["proven_auto"] == 1      # ...and the exclusion is named, not silent
