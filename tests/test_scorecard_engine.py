"""Pure scorecard engine: bucketing, action index, expression pairs, rules, build."""
import pytest
from webull_api import scorecard
from webull_api.journal.schema import ClosedTrade

CFG = scorecard.ScorecardConfig()


def _ct(symbol="AAPL", source="paper", instrument="equity", entry_fill_id=None,
        exit_fill_id=None, strategy_id=None, entry="2026-07-01T10:00:00-04:00",
        exit="2026-07-08T10:00:00-04:00", pnl=50.0, return_pct=5.0, win=True):
    return ClosedTrade(symbol=symbol, source=source, quantity=1.0, entry_price=100.0,
                       exit_price=105.0, entry_at_iso=entry, exit_at_iso=exit,
                       holding_days=7.0, pnl=pnl, return_pct=return_pct, win=win,
                       instrument=instrument, strategy_id=strategy_id,
                       entry_fill_id=entry_fill_id, exit_fill_id=exit_fill_id)


def _action(ref, source, kind="trade", symbol="AAPL", ts="2026-07-01T17:30:00-04:00", why=""):
    return {"id": ref, "ref": ref, "source": source, "kind": kind, "symbol": symbol,
            "ts": ts, "why": why, "sleeve": "paper-equity", "side": "BUY", "qty": 1}


def test_bucket_proven_wins_over_everything():
    t = _ct(strategy_id="ibs_dip", entry_fill_id="x")
    assert scorecard.bucket_of(t, {}, set()) == "proven:ibs_dip"


def test_bucket_rsi2_equity_via_entry_action():
    by_ref, days = scorecard.index_actions([_action("b1", "runner:rsi2")])
    assert scorecard.bucket_of(_ct(entry_fill_id="b1"), by_ref, days) == "rsi2-equity"


def test_bucket_rsi2_real_via_the_real_runners_attribution_row():
    """A REAL lot the rsi2_real runner adopted is the sleeve's own trade, not 'unattributed'
    and not the paper bucket (the proof bar reads rsi2-equity only)."""
    by_ref, days = scorecard.index_actions([_action("b1", "runner:rsi2_real")])
    assert scorecard.bucket_of(_ct(source="real", entry_fill_id="b1"), by_ref, days) == "rsi2-real"


def test_bucket_discretionary_when_other_source():
    by_ref, days = scorecard.index_actions([_action("b1", "chat:manager")])
    assert scorecard.bucket_of(_ct(entry_fill_id="b1"), by_ref, days) == "discretionary-equity"


def test_bucket_unattributed_when_no_action_row():
    assert scorecard.bucket_of(_ct(entry_fill_id=None), {}, set()) == "unattributed-equity"
    assert scorecard.bucket_of(_ct(entry_fill_id="missing"), {}, set()) == "unattributed-equity"


def test_bucket_options_by_entry_day_join():
    acts = [_action("o1", "runner:options_entry", symbol="GOOG",
                    ts="2026-07-13T17:30:00-04:00")]
    by_ref, days = scorecard.index_actions(acts)
    hit = _ct(symbol="GOOG", source="paper_option", instrument="option",
              entry="2026-07-13T17:30:28-04:00")
    miss = _ct(symbol="GOOG", source="paper_option", instrument="option",
               entry="2026-06-23T15:31:00-04:00")
    assert scorecard.bucket_of(hit, by_ref, days) == "rsi2-options"
    assert scorecard.bucket_of(miss, by_ref, days) == "options-discretionary"


def test_index_actions_first_ref_wins():
    a1 = _action("r1", "runner:rsi2")
    a2 = _action("r1", "chat:manager")
    by_ref, _ = scorecard.index_actions([a1, a2])
    assert by_ref["r1"]["source"] == "runner:rsi2"


def test_expression_pairs_matches_on_symbol_and_entry_date():
    eq = [_ct(symbol="GOOG", entry="2026-07-13T17:30:00-04:00", return_pct=-2.0, win=False),
          _ct(symbol="MSFT", entry="2026-07-14T17:30:00-04:00", return_pct=3.0)]
    op = [_ct(symbol="GOOG", source="paper_option", instrument="option",
              entry="2026-07-13T17:30:28-04:00", return_pct=-94.8, win=False, pnl=-474.0)]
    p = scorecard.expression_pairs(eq, op)
    assert p["pairs_n"] == 1
    assert p["equity_unpaired"] == 1 and p["options_unpaired"] == 0
    assert p["equity_mean_return_pct"] == pytest.approx(-2.0)
    assert p["options_mean_return_pct"] == pytest.approx(-94.8)
    assert p["equity_win_rate"] == 0.0 and p["options_win_rate"] == 0.0


def test_expression_pairs_empty():
    p = scorecard.expression_pairs([], [])
    assert p["pairs_n"] == 0 and p["equity_mean_return_pct"] is None


def test_build_scorecard_buckets_and_building_gate():
    acts = [_action(f"b{i}", "runner:rsi2") for i in range(3)]
    closed = [_ct(entry_fill_id=f"b{i}") for i in range(3)]
    card = scorecard.build_scorecard(closed, acts, "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    b = card["buckets"]["rsi2-equity"]
    assert b["trades"] == 3 and b["building"] is True
    assert card["date"] == "2026-07-25" and card["excluded_artifacts"] == 0
    assert card["flags"] == []          # building bucket -> rules skipped


def test_build_scorecard_excludes_artifacts():
    art = _ct(entry="2026-07-23T17:30:00-04:00", exit="2026-07-23T17:30:20-04:00")  # sub-hour
    card = scorecard.build_scorecard([art], [], "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    assert card["excluded_artifacts"] == 1 and card["buckets"] == {}


def test_rule_negative_expectancy_fires_at_gate():
    acts = [_action(f"b{i}", "runner:rsi2") for i in range(10)]
    closed = [_ct(entry_fill_id=f"b{i}", entry=f"2026-06-{i+1:02d}T10:00:00-04:00",
                  return_pct=-1.0, pnl=-10.0, win=False) for i in range(10)]
    card = scorecard.build_scorecard(closed, acts, "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    rules = [f["rule"] for f in card["flags"]]
    assert "negative_expectancy" in rules
    f = next(f for f in card["flags"] if f["rule"] == "negative_expectancy")
    assert f["severity"] == "warn" and "rsi2-equity" in f["evidence"]


def test_rule_expression_gap_fires_only_with_enough_pairs():
    days = [f"2026-06-{i+1:02d}" for i in range(8)]
    eq_acts = [_action(f"b{i}", "runner:rsi2") for i in range(8)]
    op_acts = [_action(f"o{i}", "runner:options_entry", symbol="AAPL",
                       ts=f"{d}T17:30:00-04:00") for i, d in enumerate(days)]
    # 10+ per bucket not needed: expression_gap gates on pairs_n, not bucket n
    eq = [_ct(entry_fill_id=f"b{i}", entry=f"{d}T17:30:00-04:00", return_pct=2.0,
              pnl=20.0) for i, d in enumerate(days)]
    op = [_ct(symbol="AAPL", source="paper_option", instrument="option",
              entry=f"{d}T17:30:10-04:00", return_pct=-40.0, pnl=-200.0, win=False)
          for d in days]
    card = scorecard.build_scorecard(eq + op, eq_acts + op_acts,
                                     "2026-07-25T17:30:00-04:00", "2026-07-25", CFG)
    f = next(f for f in card["flags"] if f["rule"] == "expression_gap")
    assert "options" in f["suggestion"]        # options is the lagging expression
    # with only 7 pairs the rule must NOT fire
    card2 = scorecard.build_scorecard(eq[:7] + op[:7], eq_acts[:7] + op_acts[:7],
                                      "2026-07-25T17:30:00-04:00", "2026-07-25", CFG)
    assert not any(f["rule"] == "expression_gap" for f in card2["flags"])


def test_rule_stop_slippage_and_option_breach():
    # 5 equity stop exits averaging -11% (plan -8, slack 2 -> threshold -10)
    stop_why = "AAPL down past your -8% stop (unrealized -11.0%)."
    acts = ([_action(f"b{i}", "runner:rsi2") for i in range(5)]
            + [_action(f"s{i}", "runner:paper_eod", kind="exit", why=stop_why)
               for i in range(5)])
    eq = [_ct(entry_fill_id=f"b{i}", exit_fill_id=f"s{i}",
              entry=f"2026-06-{i+1:02d}T10:00:00-04:00", return_pct=-11.0,
              pnl=-110.0, win=False) for i in range(5)]
    breach = _ct(symbol="GOOG", source="paper_option", instrument="option",
                 entry="2026-07-13T17:30:00-04:00", return_pct=-94.8, pnl=-474.0,
                 win=False)
    card = scorecard.build_scorecard(eq + [breach], acts,
                                     "2026-07-25T17:30:00-04:00", "2026-07-25", CFG)
    rules = [f["rule"] for f in card["flags"]]
    assert "stop_slippage" in rules and "option_stop_breach" in rules


def test_rule_vol_unchecked_share():
    acts = [_action(f"o{i}", "runner:options_entry", symbol="AAPL",
                    ts=f"2026-07-{i+1:02d}T17:30:00-04:00",
                    why="RSI(2)=3 < 10; opened AAPL debit call vertical; IV/HV n/a")
            for i in range(10)]
    card = scorecard.build_scorecard([], acts, "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    assert any(f["rule"] == "vol_unchecked_share" for f in card["flags"])
    # fewer than the window -> gated off
    card2 = scorecard.build_scorecard([], acts[:9], "2026-07-25T17:30:00-04:00",
                                      "2026-07-25", CFG)
    assert not any(f["rule"] == "vol_unchecked_share" for f in card2["flags"])


def test_rule_fresh_artifacts_window():
    fresh = _ct(entry="2026-07-23T17:30:00-04:00", exit="2026-07-23T17:30:20-04:00")
    stale = _ct(entry="2026-07-01T17:30:00-04:00", exit="2026-07-01T17:30:20-04:00")
    card = scorecard.build_scorecard([fresh, stale], [], "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    f = next(f for f in card["flags"] if f["rule"] == "fresh_artifacts")
    assert "1" in f["evidence"]     # only the fresh one is in the 7-day window


def test_rule_option_breach_sees_proven_buckets():
    breach = _ct(symbol="GOOG", source="paper_option", instrument="option",
                 strategy_id="lab_strat", entry="2026-07-13T17:30:00-04:00",
                 return_pct=-94.8, pnl=-474.0, win=False)
    card = scorecard.build_scorecard([breach], [], "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    assert any(f["rule"] == "option_stop_breach" for f in card["flags"])


def test_rule_stop_slippage_sees_all_equity_buckets():
    stop_why = "AAPL down past your -8% stop (unrealized -11.0%)."
    acts = [_action(f"s{i}", "runner:paper_eod", kind="exit", why=stop_why)
            for i in range(5)]
    eq = [_ct(strategy_id="lab_strat", exit_fill_id=f"s{i}",
              entry=f"2026-06-{i+1:02d}T10:00:00-04:00", return_pct=-11.0,
              pnl=-110.0, win=False) for i in range(5)]
    card = scorecard.build_scorecard(eq, acts, "2026-07-25T17:30:00-04:00",
                                     "2026-07-25", CFG)
    assert any(f["rule"] == "stop_slippage" for f in card["flags"])
