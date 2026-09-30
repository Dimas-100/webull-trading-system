import json

from webull_api.lab import orchestrator
from webull_api.lab.schema import (LAB_BASKET, OBJECTIVE_TEXT, DEFAULT_LAB_CONFIG, ProposalRecord,
                                   RegimeTag, ReseedResult, SeedEntry, StrategyRecord,
                                   WalkForwardReport, WindowResult)
from webull_api.strategy.schema import RsiCond, SmaCross, Strategy


def _strat(name, entry):
    return Strategy(name=name, symbol="AAPL", entry=entry)


def _rec(rec_id, status, entry, *, with_folds=False):
    rep = None
    if with_folds:
        rep = WalkForwardReport(
            folds=[WindowResult(fold_index=0, start_index=0, end_index=10,
                                regime=RegimeTag(trend="up", vol="low"), num_trades=5,
                                expectancy=1.0, avg_trade_return_pct=0.5, profit_factor=1.4,
                                win_rate=0.6, return_pct=3.0, max_drawdown_pct=-4.0,
                                ulcer_index=1.0, conditional_drawdown_95=-3.0, sharpe=1.0,
                                sortino=1.3),
                   WindowResult(fold_index=1, start_index=10, end_index=20,
                                regime=RegimeTag(trend="down", vol="high"), num_trades=4,
                                expectancy=-0.5, avg_trade_return_pct=-0.2, profit_factor=0.8,
                                win_rate=0.3, return_pct=-2.0, max_drawdown_pct=-9.0,
                                ulcer_index=3.0, conditional_drawdown_95=-7.0, sharpe=-0.3,
                                sortino=-0.4)])
    return StrategyRecord(id=rec_id, strategy=_strat(rec_id, entry), fingerprint=rec_id,
                          canon_bucket=rec_id, archetype="trend_follow",
                          cohort="trend_follow:up/low", status=status,
                          created_at_iso="2026-01-01", as_of="2026-01-01",
                          gate_a=rep, lineage=ProposalRecord())


def _reseed_result():
    s = _strat("seed", SmaCross(type="sma_cross", fast=10, slow=20, direction="above"))
    return ReseedResult(seeds=[SeedEntry(fingerprint="seed", strategy=s, archetype="trend_follow",
                                         why="mutated winner", status="confirmed_proven")],
                        explore_quota=2)


def _brief():
    lib = [
        _rec("p", "proving", SmaCross(type="sma_cross", fast=10, slow=20, direction="above"), with_folds=True),
        _rec("k", "rejected", RsiCond(type="rsi", period=14, threshold=30, comparison="below")),
    ]
    tombstones = [{"fingerprint": "ga_fail_1", "fail_codes": ["dsr_below_floor", "breadth_fail"]}]
    return orchestrator.build_proposal_brief(lib, "2026-06-29T00:00:00",
                                             regime_now=RegimeTag(trend="up", vol="low"),
                                             reseed_result=_reseed_result(),
                                             cfg=DEFAULT_LAB_CONFIG, tombstones=tombstones)


def test_brief_has_expected_context():
    b = _brief()
    assert b.universe == list(LAB_BASKET)
    assert b.objective == OBJECTIVE_TEXT
    assert b.regime_now.bucket() == "up/low"
    assert b.explore_quota == 2
    assert [s.fingerprint for s in b.seeds] == ["seed"]
    assert "p" in b.avoid_fingerprints and "ga_fail_1" in b.avoid_fingerprints
    assert "dsr_below_floor" in b.tombstone_patterns
    assert any(a.archetype == "trend_follow" for a in b.archetype_stats)


def test_lab_brief_no_future_leak():
    b = _brief()
    blob = json.loads(b.model_dump_json())

    forbidden_keys = {"equity_curve", "live_equity_curve", "live_trades", "bars", "closes",
                      "prices", "holdout", "lockbox", "expectancy_ci", "forward_expectancy_ci",
                      "live_metrics", "oos_stats", "per_candidate"}
    numeric_series = []

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, f"leaked field: {k}"
                walk(v)
        elif isinstance(node, list):
            if len(node) >= 3 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in node):
                numeric_series.append(node)
            for v in node:
                walk(v)

    walk(blob)
    assert numeric_series == [], f"brief leaked a numeric time-series: {numeric_series}"
