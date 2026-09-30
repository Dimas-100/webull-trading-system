"""Loop seam #2 (slice 2b): the read-only live-vs-proven comparison — each proven strategy's LIVE
attributed-paper stats vs its Gate-B (simulated) forward proof, with a holding/decayed read. Pure."""
from types import SimpleNamespace

from webull_api.journal import pairing
from webull_api.journal.schema import Fill
from webull_api.lab import lab_analytics


def _proven(name, tid, proof_exp):
    return SimpleNamespace(strategy=SimpleNamespace(name=name),
                           gate_b={"trial_id": tid, "live_metrics": {"expectancy": proof_exp}},
                           graduated_at="2026-07-01T00:00:00Z")


def _closed(tid, buy, sell):
    """One attributed round-trip (BUY tagged with trial_id `tid`, then a SELL) -> a ClosedTrade."""
    fills = [
        Fill(id=f"{tid}-b", source="paper", account_id="proven", symbol="AAA", side="BUY",
             quantity=1, price=buy, filled_at_iso="2026-07-01T10:00:00-04:00", order_type="MARKET",
             trial_id=tid),
        Fill(id=f"{tid}-s", source="paper", account_id="proven", symbol="AAA", side="SELL",
             quantity=1, price=sell, filled_at_iso="2026-07-08T10:00:00-04:00", order_type="MARKET"),
    ]
    closed, _ = pairing.pair_fills(fills)
    return closed


def test_no_live_data_when_the_strategy_has_not_traded():
    rows = lab_analytics.live_vs_proven_rows([_proven("S1", "T1", 5.0)], [])
    assert rows[0]["verdict"] == "no_live_data" and rows[0]["live"]["trades"] == 0
    assert rows[0]["proof_expectancy"] == 5.0


def test_holding_when_live_edge_is_at_least_half_the_proof():
    closed = _closed("T1", 10.0, 14.0)  # +$4/trade live vs $5 proof (>= 0.5x) -> holding
    rows = lab_analytics.live_vs_proven_rows([_proven("S1", "T1", 5.0)], closed)
    assert rows[0]["verdict"] == "holding" and rows[0]["live"]["trades"] == 1


def test_decayed_when_live_edge_falls_below_half_the_proof():
    closed = _closed("T1", 10.0, 8.0)  # -$2/trade live -> decayed
    rows = lab_analytics.live_vs_proven_rows([_proven("S1", "T1", 5.0)], closed)
    assert rows[0]["verdict"] == "decayed"


def test_attribution_is_matched_by_trial_id():
    # a trade tagged for a DIFFERENT trial must not count toward this strategy
    closed = _closed("OTHER", 10.0, 14.0)
    rows = lab_analytics.live_vs_proven_rows([_proven("S1", "T1", 5.0)], closed)
    assert rows[0]["live"]["trades"] == 0 and rows[0]["verdict"] == "no_live_data"
