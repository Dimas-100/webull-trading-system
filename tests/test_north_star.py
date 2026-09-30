from webull_api import north_star as ns


def test_lab_block_chip_producing_when_proven():
    b = ns.lab_block({"proven": 2, "proven_names": ["A", "B"], "in_flight": 1,
                      "cycles_run": 5, "last_cycle_date": "2026-07-08", "stale": False})
    assert b["chip"] == "producing"
    assert b["proven"] == 2 and b["proven_names"] == ["A", "B"]


def test_lab_block_chip_testing_when_none_proven_and_fresh():
    b = ns.lab_block({"proven": 0, "cycles_run": 5, "stale": False})
    assert b["chip"] == "testing"


def test_lab_block_chip_stale_takes_over_when_no_recent_cycle():
    b = ns.lab_block({"proven": 0, "stale": True})
    assert b["chip"] == "stale"


def test_lab_block_unavailable_is_unknown_with_full_keys():
    b = ns.lab_block({"unavailable": True})
    assert b["chip"] == "unknown"
    assert set(b) >= {"proven", "in_flight", "cycles_run", "last_cycle_date", "stale", "proven_names"}


def test_paper_block_combines_pnl():
    b = ns.paper_block({"equity_realized_pnl": 498.70, "options_realized_pnl": -250.0,
                        "open_positions": 4, "decisions": 6, "expectancy": 41.45, "win_rate": 0.83})
    assert round(b["combined_realized_pnl"], 2) == 248.70
    assert b["decisions_target"] == 30 and b["chip"] == "active"


def test_paper_block_unavailable_is_unknown():
    b = ns.paper_block({"unavailable": True})
    assert b["chip"] == "unknown" and b["combined_realized_pnl"] == 0.0


def test_proof_bar_thresholds():
    assert ns.proof_bar(29, 10.0, 0.5)["sample_met"] is False
    assert ns.proof_bar(30, 10.0, 0.5)["sample_met"] is True
    assert ns.proof_bar(30, 0.0, 0.5)["edge_met"] is False
    pb = ns.proof_bar(30, 5.0, 0.6)
    assert pb["machine_met"] is True and pb["win_rate"] == 0.6


def test_proof_bar_edge_requires_both_units_positive():
    # size-independent honesty: a mixed-size sample whose $ and % expectancy disagree in
    # sign is a red flag, not a pass.
    assert ns.proof_bar(30, 5.0, 0.6, expectancy_pct=-0.2)["edge_met"] is False
    assert ns.proof_bar(30, -5.0, 0.6, expectancy_pct=0.2)["edge_met"] is False
    pb = ns.proof_bar(30, 5.0, 0.6, expectancy_pct=1.1)
    assert pb["edge_met"] is True and pb["machine_met"] is True
    assert pb["expectancy_pct"] == 1.1
    # pct omitted -> dollar-only compat (old callers unchanged)
    assert ns.proof_bar(30, 5.0, 0.6)["edge_met"] is True


def test_paper_block_passes_through_pct_and_exclusions():
    b = ns.paper_block({"equity_realized_pnl": 1.0, "options_realized_pnl": 0.0,
                        "open_positions": 0, "decisions": 13, "expectancy": 112.07,
                        "expectancy_pct": 1.9, "excluded_artifacts": 3, "win_rate": 0.6})
    assert b["expectancy_pct"] == 1.9 and b["excluded_artifacts"] == 3
    unavailable = ns.paper_block({"unavailable": True})
    assert unavailable["expectancy_pct"] == 0.0 and unavailable["excluded_artifacts"] == 0


def test_readiness_not_cleared_when_underfunded():
    r = ns.readiness(funded=False, machine_met=True, reviewed=True,
                     enabled=False, kill_active=False, ever_placed=False)
    assert r["cleared_to_go_live"] is False
    assert r["blockers_remaining"] == 1  # only funded is missing


def test_readiness_cleared_when_all_prereqs_met():
    r = ns.readiness(funded=True, machine_met=True, reviewed=True,
                     enabled=True, kill_active=False, ever_placed=False)
    assert r["cleared_to_go_live"] is True and r["safety_flag"] is False
    armed = next(i for i in r["checklist"] if i["key"] == "armed")
    assert armed["state"] == "done"


def test_readiness_safety_flag_when_enabled_before_gate_complete():
    r = ns.readiness(funded=False, machine_met=False, reviewed=False,
                     enabled=True, kill_active=False, ever_placed=False)
    assert r["safety_flag"] is True
    armed = next(i for i in r["checklist"] if i["key"] == "armed")
    assert armed["state"] == "attention"
    assert r["blockers_remaining"] == 3


def test_readiness_funded_unknown_when_none():
    r = ns.readiness(funded=None, machine_met=False, reviewed=False,
                     enabled=False, kill_active=False, ever_placed=False)
    funded = next(i for i in r["checklist"] if i["key"] == "funded")
    assert funded["state"] == "unknown"
    assert r["cleared_to_go_live"] is False


def test_readiness_chip_live_when_ever_placed():
    r = ns.readiness(funded=True, machine_met=True, reviewed=True,
                     enabled=True, kill_active=False, ever_placed=True)
    assert r["chip"] == "live"


def test_readiness_unavailable_all_unknown():
    r = ns.readiness_unavailable()
    assert r["chip"] == "unknown"
    assert all(i["state"] == "unknown" for i in r["checklist"])
    assert r["safety_flag"] is False and r["cleared_to_go_live"] is False
    assert [i["key"] for i in r["checklist"]] == ["funded", "proof_bar", "review", "armed"]


def test_scorecard_assembles_three_blocks():
    sc = ns.scorecard(lab={"chip": "x"}, paper={"chip": "y"}, real={"chip": "z"})
    assert set(sc) == {"lab", "paper", "real"}


# ---- proof_bar_sample: the code-enforced proof-bar-read-scope decision (ledger 2026-07-28) ----
def _ct(sym, entry_at, fill_id=None, strategy_id=None, pnl=1.0):
    from webull_api.journal.schema import ClosedTrade
    return ClosedTrade(symbol=sym, source="paper", quantity=1, entry_price=100.0,
                       exit_price=100.0 + pnl, entry_at_iso=entry_at,
                       exit_at_iso="2026-08-14T17:30:00-04:00", holding_days=1.0,
                       pnl=pnl, return_pct=1.0, win=pnl > 0,
                       entry_fill_id=fill_id, strategy_id=strategy_id)


_RSI2_ACTIONS = [{"ref": "a", "source": "runner:rsi2", "kind": "trade"},
                 {"ref": "b", "source": "runner:rsi2", "kind": "trade"},
                 {"ref": "c", "source": "runner:rsi2", "kind": "trade"}]


def test_proof_bar_cutoff_matches_the_ledger_decision():
    # proof-bar-read-scope: entries at/after the 2026-07-24 17:30 ET coordination fix.
    assert ns.PROOF_BAR_ENTRY_CUTOFF.isoformat() == "2026-07-24T17:30:00-04:00"


def test_proof_bar_sample_keeps_only_post_fix_rsi2_rows():
    rows = [_ct("AAPL", "2026-08-01T17:30:00-04:00", "a"),                    # the bar
            _ct("MSFT", "2026-07-20T17:30:00-04:00", "b"),                    # rsi2 but pre-fix
            _ct("VOO", "2026-08-01T17:30:00-04:00", None),                    # legacy/unattributed
            _ct("SPY", "2026-08-01T17:30:00-04:00", None, strategy_id="s1")]  # proven auto-trade
    kept, ctx = ns.proof_bar_sample(rows, _RSI2_ACTIONS)
    assert [t.symbol for t in kept] == ["AAPL"]
    assert ctx == {"pre_fix_rsi2": 1, "legacy_equity": 1, "proven_auto": 1,
                   "unparseable_entry": 0}


def test_proof_bar_sample_boundary_entry_at_cutoff_is_kept():
    # "at/after" — a naive timestamp is treated as ET.
    kept, _ = ns.proof_bar_sample([_ct("WMT", "2026-07-24T17:30:00", "a")], _RSI2_ACTIONS)
    assert [t.symbol for t in kept] == ["WMT"]


def test_proof_bar_sample_fails_closed_on_unparseable_entry():
    # A row that cannot prove it is post-fix does not count toward real-money evidence.
    rows = [_ct("AAPL", "not-a-date", "a"), _ct("NVDA", "", "b"),
            _ct("WMT", "2026-08-01T17:30:00-04:00", "c")]
    kept, ctx = ns.proof_bar_sample(rows, _RSI2_ACTIONS)
    assert [t.symbol for t in kept] == ["WMT"]
    assert ctx["unparseable_entry"] == 2


def test_paper_block_passes_context_through():
    b = ns.paper_block({"equity_realized_pnl": 1.0, "options_realized_pnl": 0.0,
                        "context": {"legacy_equity": 3, "options": 2}})
    assert b["context"] == {"legacy_equity": 3, "options": 2}


def test_funding_target_matches_the_amended_owner_decision():
    # rsi2-live-stay-paper (amended 2026-08-03): owner target ~$1.5k; $400 was superseded and
    # must not light the funded check (real net-liq crossed $400 already).
    assert ns.FUNDING_TARGET == 1500.0
