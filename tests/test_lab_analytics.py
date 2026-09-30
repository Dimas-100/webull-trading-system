from __future__ import annotations

import pathlib

import pytest

from webull_api.lab.schema import LabSummary, StrategyRecord, TrialBook
from webull_api.strategy.schema import BacktestResult, EquityPoint, Strategy, Trade
from webull_api.lab import lab_analytics


def _strat(name="alpha"):
    return Strategy(name=name, symbol="SPY",
                    entry={"type": "sma_cross", "fast": 2, "slow": 3, "direction": "above"})


def _record(rid, name, *, gen, cohort, trial_id):
    return StrategyRecord(id=rid, strategy=_strat(name), fingerprint=f"fp-{rid}",
                          canon_bucket=f"cb-{rid}", archetype="trend_follow", cohort=cohort,
                          generation=gen, behavioral_cohort=f"bc-{rid}",
                          created_at_iso="2026-01-01T00:00:00", as_of="2026-06-01", trial_id=trial_id)


def _trade(pnl):
    return Trade(entry_time="2026-01-02", entry_price=100.0, exit_time="2026-01-09",
                 exit_price=100.0 + pnl, shares=1.0, pnl=float(pnl), return_pct=float(pnl),
                 exit_reason="signal")


def _book(trades):
    curve = [EquityPoint(time="2026-01-02", equity=10000.0),
             EquityPoint(time="2026-01-09", equity=10000.0 + sum(t.pnl for t in trades))]
    metrics = BacktestResult(total_return_pct=0.0, buy_hold_return_pct=0.0, num_trades=len(trades),
                             win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0, expectancy=0.0,
                             max_drawdown_pct=0.0, equity_curve=curve, trades=trades)
    return TrialBook(scope="portfolio", starting_equity=10000.0, equity_curve=curve, metrics=metrics,
                     trades_by_symbol={"SPY": trades}, per_symbol_metrics={"SPY": metrics},
                     forward_trades=len(trades), forward_bars=130)


# ---- trial_trade_to_closed ----
def test_trial_trade_to_closed_stamps_lab_provenance():
    rec = _record("rec1", "alpha", gen=2, cohort="trend_follow:up/low", trial_id="t1")
    ct = lab_analytics.trial_trade_to_closed(_trade(50.0), rec, "AAPL")
    assert ct.source == "paper_trial" and ct.instrument == "equity"
    assert ct.symbol == "AAPL" and ct.setup == "alpha"
    assert ct.strategy_id == "rec1" and ct.generation == 2
    assert ct.cohort == "trend_follow:up/low" and ct.behavioral_cohort == "bc-rec1"
    assert ct.trial_id == "t1" and ct.win is True and ct.pnl == 50.0


# ---- normalize_curve ----
def test_normalize_curve_to_pct_of_start():
    out = lab_analytics.normalize_curve(
        [EquityPoint(time="a", equity=10000.0), EquityPoint(time="b", equity=11000.0)], 10000.0)
    assert [p.equity for p in out] == pytest.approx([100.0, 110.0])


# ---- build_lab_summary ----
def test_build_lab_summary_partitions_by_strategy():
    r1 = _record("rec1", "alpha", gen=1, cohort="trend_follow:up/low", trial_id="t1")
    r2 = _record("rec2", "beta", gen=2, cohort="mean_revert:side/low", trial_id="t2")
    books = {"t1": _book([_trade(50.0), _trade(-20.0)]), "t2": _book([_trade(30.0)])}
    summary = lab_analytics.build_lab_summary([r1, r2], books, generated_at_iso="2026-06-29")
    assert isinstance(summary, LabSummary)
    assert summary.overall.trades == 3                       # 2 + 1 pooled
    by_strat = {b.key for b in summary.by_strategy}
    assert by_strat == {"rec1", "rec2"}                      # partitioned by strategy_id
    assert {t.id for t in summary.trials} == {"rec1", "rec2"}
    assert {b.key for b in summary.by_generation} == {"1", "2"}


def test_build_lab_summary_empty_is_zeroed_no_crash():
    summary = lab_analytics.build_lab_summary([], {})
    assert summary.overall.trades == 0 and summary.trials == []


def test_lab_analytics_does_no_file_io():
    """Pure analytics layer must not touch the shared paper/journal stores (M6 enforces byte-unchanged
    files end-to-end; here we lock the lighter invariant that the module imports no *_store)."""
    src = pathlib.Path(lab_analytics.__file__).read_text(encoding="utf-8")
    assert "store" not in src and "open(" not in src


# ---- FAIL_CODE_LABELS ----
def test_fail_code_labels_cover_the_four_remaining_gate_a_codes():
    # daily_only_v1/insufficient_history/cost_fragile/param_fragile are real codes emitted by
    # gate_a.screen_one (see gate_a.py fails.append sites) but were missing owner-facing labels,
    # so the funnel/near-miss UI fell back to the raw code string for them.
    for code in ("daily_only_v1", "insufficient_history", "cost_fragile", "param_fragile"):
        assert code in lab_analytics.FAIL_CODE_LABELS
        assert lab_analytics.FAIL_CODE_LABELS[code] != code   # a real label, not just the code


# ---- funnel_summary ----
def _funnel_cycle(seq, *, gen=8, screened=8, accepted=(), gaf=(), promoted=(), killed=(),
                  date="2026-07-01"):
    # gaf entries: (fingerprint, codes) or (fingerprint, codes, metrics_dict)
    return {"cycle_seq": seq, "cycle_date": date, "generated": gen, "screened": screened,
            "duplicates": 0, "accepted": list(accepted), "new_proving": list(accepted),
            "promoted": list(promoted), "killed": list(killed),
            "gate_a_failed": [{"fingerprint": e[0], "fail_codes": list(e[1]),
                               **(e[2] if len(e) > 2 else {})} for e in gaf]}


class TestFunnelSummary:
    def test_empty_store_is_valid(self):
        out = lab_analytics.funnel_summary([])
        assert out["rows"] == [] and out["latest"] is None
        assert out["lifetime"] == {"cycles_run": 0, "generated": 0, "screened": 0,
                                   "accepted": 0, "proven": 0}
        assert out["fail_codes"] == [] and out["top_blockers"] == [] and out["near_misses"] == []

    def test_aggregates_labels_and_latest(self):
        cycles = [
            _funnel_cycle(1, gaf=[("f1", ("ci_lb_negative", "edge_below_floor", "breadth_fail"))]),
            _funnel_cycle(2, accepted=("acc1",),
                          gaf=[("f2", ("ci_lb_negative",)),
                               ("f3", ("ci_lb_negative", "dsr_below_floor"))]),
        ]
        out = lab_analytics.funnel_summary(cycles, proven_total=1)
        assert out["lifetime"]["cycles_run"] == 2
        assert out["lifetime"]["accepted"] == 1 and out["lifetime"]["proven"] == 1
        top = out["fail_codes"][0]
        assert top["code"] == "ci_lb_negative" and top["lifetime"] == 3 and top["latest"] == 2
        assert top["label"] == lab_analytics.FAIL_CODE_LABELS["ci_lb_negative"]
        assert out["top_blockers"][0] == top["label"] and len(out["top_blockers"]) == 2
        assert out["latest"] == {"cycle_seq": 2, "date": "2026-07-01", "screened": 8, "accepted": 1}
        assert [r["cycle_seq"] for r in out["rows"]] == [1, 2]

    def test_near_misses_fewest_codes_first_le2_codes_capped_at_10(self):
        # margin-aware contract (2026-08-15): fewest fail codes first (1-code beats 2-code),
        # newest cycle breaking ties; >2 codes never a near-miss; capped at 10.
        cycles = [_funnel_cycle(1, gaf=[("old", ("ci_lb_negative",))]),
                  _funnel_cycle(2, gaf=[("newer", ("ci_lb_negative", "pf_below_floor")),
                                        ("threecodes", ("a", "b", "c"))])]
        out = lab_analytics.funnel_summary(cycles)
        assert [n["fingerprint"] for n in out["near_misses"]] == ["old", "newer"]
        many = [_funnel_cycle(i, gaf=[(f"nm{i}", ("ci_lb_negative",))]) for i in range(1, 15)]
        nm = lab_analytics.funnel_summary(many)["near_misses"]
        assert len(nm) == 10
        assert nm[0]["fingerprint"] == "nm14"          # same codes, no metrics -> newest first

    def test_near_misses_sorted_by_dsr_gap_with_metrics_attached(self):
        # Within a code-count group the smallest DSR shortfall ranks first; entries without
        # metrics (pre-2026-08-15 rows) sort last in their group; stored metrics ride along
        # and the payload names the floor the gap is judged against.
        cycles = [_funnel_cycle(1, gaf=[
            ("far", ("dsr_below_floor",), {"dsr": 0.60, "pooled_trades": 120}),
            ("close", ("dsr_below_floor",), {"dsr": 0.94, "ci_lb": 0.01}),
            ("legacy", ("dsr_below_floor",)),
            ("twocode", ("dsr_below_floor", "pf_below_floor"), {"dsr": 0.99})])]
        out = lab_analytics.funnel_summary(cycles)
        assert [n["fingerprint"] for n in out["near_misses"]] == \
            ["close", "far", "legacy", "twocode"]
        assert out["near_misses"][0]["dsr"] == 0.94
        assert out["near_misses"][0]["ci_lb"] == 0.01
        assert out["near_misses"][1]["pooled_trades"] == 120
        assert out["dsr_floor"] == 0.95

    def test_malformed_gate_a_entries_skipped(self):
        c = _funnel_cycle(1)
        c["gate_a_failed"] = ["notadict", {"fingerprint": "x", "fail_codes": None}]
        out = lab_analytics.funnel_summary([c])
        assert out["fail_codes"] == [] and out["near_misses"] == []

    def test_unknown_code_falls_back_to_raw_code(self):
        out = lab_analytics.funnel_summary([_funnel_cycle(1, gaf=[("f", ("brand_new_code",))])])
        assert out["fail_codes"][0]["label"] == "brand_new_code"

    def test_rows_windowed_by_recent(self):
        cycles = [_funnel_cycle(i) for i in range(1, 20)]
        out = lab_analytics.funnel_summary(cycles, recent=5)
        assert [r["cycle_seq"] for r in out["rows"]] == [15, 16, 17, 18, 19]
        assert out["lifetime"]["cycles_run"] == 19          # lifetime spans ALL cycles
