"""Pure slippage math: reference chain, sign convention, aggregates."""
from webull_api import exec_quality


def _d(**kw):
    base = {"id": "X", "symbol": "AAPL", "side": "BUY", "order_type": "MARKET",
            "limit_price": None, "stop_price": None, "mark": None}
    base.update(kw)
    return base


def _f(fid, price, at="2026-07-21T16:00:00"):
    return {"id": fid, "price": price, "filled_at_iso": at}


def test_market_order_measures_vs_mark_buy_above_is_cost():
    rows = exec_quality.slippage_rows([_d(id="A", mark=100.0)], [_f("A", 100.1)])
    assert rows[0]["ref_kind"] == "mark" and rows[0]["signed_bp"] == 10.0


def test_sell_below_ref_is_cost_and_sell_above_is_improvement():
    d = _d(id="A", side="SELL", mark=100.0)
    assert exec_quality.slippage_rows([d], [_f("A", 99.9)])[0]["signed_bp"] == 10.0
    assert exec_quality.slippage_rows([d], [_f("A", 100.1)])[0]["signed_bp"] == -10.0


def test_reference_chain_prefers_stop_then_limit_then_mark():
    d = _d(id="A", order_type="STOP_LOSS_LIMIT", stop_price="50", limit_price="50.5", mark=49.0)
    rows = exec_quality.slippage_rows([d], [_f("A", 50.25)])
    assert rows[0]["ref_kind"] == "stop" and rows[0]["ref"] == 50.0
    d2 = _d(id="B", order_type="LIMIT", limit_price="100", mark=99.0)
    assert exec_quality.slippage_rows([d2], [_f("B", 100.0)])[0]["ref_kind"] == "limit"


def test_unmatched_and_referenceless_rows_are_skipped():
    assert exec_quality.slippage_rows([_d(id="A", mark=100.0)], []) == []
    assert exec_quality.slippage_rows([_d(id="B")], [_f("B", 10.0)]) == []


def test_summary_aggregates_and_zeroes_empty():
    rows = exec_quality.slippage_rows(
        [_d(id="A", mark=100.0), _d(id="B", side="SELL", mark=200.0)],
        [_f("A", 100.2), _f("B", 200.0)])
    s = exec_quality.summary(rows, awaiting=3)
    assert s["fills_matched"] == 2 and s["awaiting_fill"] == 3
    assert s["avg_bp"] == 10.0 and s["median_bp"] == 10.0
    assert s["worst"]["symbol"] == "AAPL" and s["worst"]["signed_bp"] == 20.0
    assert s["by_order_type"]["MARKET"] == {"n": 2, "avg_bp": 10.0}
    assert s["modeled_bp"] == 10.0     # gate_a stress: max(2×0.05%, 0.10%) = 10bp per side
    z = exec_quality.summary([], awaiting=0)
    assert z["fills_matched"] == 0 and z["worst"] is None and z["avg_bp"] == 0.0


def test_modeled_bp_matches_gate_a_stress_cost():
    from webull_api.lab.gate_a import stress_cost
    assert exec_quality.summary([])["modeled_bp"] == stress_cost().slippage_pct * 100.0


def test_rows_sorted_chronologically():
    ds = [_d(id="B", mark=100.0), _d(id="A", mark=100.0)]
    fs = [_f("B", 100.1, at="2026-07-22T10:00:00"), _f("A", 100.1, at="2026-07-21T10:00:00")]
    assert [r["id"] for r in exec_quality.slippage_rows(ds, fs)] == ["A", "B"]
