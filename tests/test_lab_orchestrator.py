import pytest

from webull_api.lab import gate_a, orchestrator
from webull_api.lab.schema import (DEFAULT_GATE_A_CONFIG, DEFAULT_LAB_CONFIG, LAB_BASKET, LabConfig,
                                   ProposalRecord, StrategyRecord, WalkForwardReport)
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.cost import NO_COST
from webull_api.strategy.schema import PriceVsSma, SmaCross, Strategy


def _ok_bars(sym="X", tf="D", count="0"):
    return [{"time": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]


def test_fetch_basket_bars_tolerates_one_flaky_symbol():
    # #3: one flaky symbol (common on a home connection) must NOT abort the whole cycle.
    bad = LAB_BASKET[3]

    def gb(sym, tf="D", count="0"):
        if sym == bad:
            raise RuntimeError("transient network")
        return _ok_bars(sym)
    bars, dropped = orchestrator.fetch_basket_bars(gb, count=100)
    assert dropped == [bad]
    assert bad not in bars and len(bars) == len(LAB_BASKET) - 1


def test_fetch_basket_bars_aborts_below_quorum():
    # a broad outage (half the basket down) is a real failure -> hard abort, not a breadth-starved run.
    down = set(LAB_BASKET[: len(LAB_BASKET) // 2])

    def gb(sym, tf="D", count="0"):
        if sym in down:
            raise RuntimeError("outage")
        return _ok_bars(sym)
    with pytest.raises(orchestrator.InsufficientBasketData):
        orchestrator.fetch_basket_bars(gb, count=100, min_ok_frac=0.75)


def test_fetch_basket_bars_propagates_entitlement():
    # an entitlement error is distinct from a flaky symbol -> it propagates (hard abort), never dropped.
    def gb(sym, tf="D", count="0"):
        raise MarketDataNotEntitledError("not entitled")
    with pytest.raises(MarketDataNotEntitledError):
        orchestrator.fetch_basket_bars(gb, count=100)


def test_first_available_bars_skips_failures():
    # the regime probe uses the first symbol that fetches, so a down reference symbol doesn't abort.
    down = set(LAB_BASKET[:2])

    def gb(sym, tf="D", count="0"):
        if sym in down:
            raise RuntimeError("down")
        return _ok_bars(sym)
    assert orchestrator.first_available_bars(gb, count=100)


def _cand(name, fast=10, slow=20):
    return Strategy(name=name, symbol="AAPL",
                    entry=SmaCross(type="sma_cross", fast=fast, slow=slow, direction="above"))


def _bars(*_a, **_k):
    return [{"time": "2026-01-01", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]


def _patch_screen(monkeypatch, pass_names):
    def fake(strategy, bars_by_symbol, *, cfg, cost, m, require_cost_robust=False,
             lockbox_bars_by_symbol=None):
        ok = strategy.name in pass_names
        return WalkForwardReport(passed=ok, fail_codes=([] if ok else ["dsr_below_floor"]),
                                 regime_buckets=["up/low"], m_at_eval=m)
    monkeypatch.setattr(gate_a, "screen_one", fake)


def test_ingest_admits_pass_tombstones_fail_and_increments_m(monkeypatch):
    _patch_screen(monkeypatch, pass_names={"good1", "good2"})
    lib = []
    res = orchestrator.ingest_proposals(
        lib, [_cand("good1", 10, 20), _cand("good2", 5, 30), _cand("bad", 8, 40)],
        "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
        cost=NO_COST, get_bars=_bars, m=0)
    assert len(res.accepted) == 2
    assert len(res.gate_a_failed) == 1 and res.gate_a_failed[0]["fail_codes"] == ["dsr_below_floor"]
    assert res.m_after == 3                       # +1 per candidate campaign
    assert len([r for r in lib if r.status == "proving"]) == 2


def test_ingest_dedups_structural_and_skips_m(monkeypatch):
    _patch_screen(monkeypatch, pass_names={"a"})
    lib = []
    orchestrator.ingest_proposals(lib, [_cand("a")], "2026-06-29T00:00:00",
                                  cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
                                  cost=NO_COST, get_bars=_bars, m=0)
    res2 = orchestrator.ingest_proposals(lib, [_cand("a-renamed")], "2026-06-29T00:00:00",
                                         cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
                                         cost=NO_COST, get_bars=_bars, m=1)
    assert res2.accepted == [] and len(res2.duplicates) == 1
    assert res2.m_after == 1                       # a duplicate runs no Gate A campaign


def test_ingest_caps_batch(monkeypatch):
    _patch_screen(monkeypatch, pass_names=set())   # all fail -> count campaigns via m
    cfg = LabConfig(max_candidates_per_batch=2)
    res = orchestrator.ingest_proposals(
        [], [_cand("c1", 2, 9), _cand("c2", 3, 9), _cand("c3", 4, 9)], "2026-06-29T00:00:00",
        cfg=cfg, gate_cfg=DEFAULT_GATE_A_CONFIG, cost=NO_COST, get_bars=_bars, m=0)
    assert res.m_after == 2                         # only 2 of 3 campaigns ran


def test_m_is_monotone_across_a_simulated_reseed_loop(monkeypatch):
    _patch_screen(monkeypatch, pass_names={"r1", "r2", "r3"})
    lib, m = [], 0
    # r1(2,9)→canon(0,10), r2(3,9)→canon(5,10), r3(3,20)→canon(5,20): all distinct canon_buckets
    # (fast=3 and fast=4 both bucket to 5 — the plan's (4,9) collided with r2; fixed by varying slow)
    res1 = orchestrator.ingest_proposals(lib, [_cand("r1", 2, 9), _cand("r2", 3, 9), _cand("r3", 3, 20)],
                                         "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG,
                                         gate_cfg=DEFAULT_GATE_A_CONFIG, cost=NO_COST,
                                         get_bars=_bars, m=m)
    m = res1.m_after
    # reseed round: re-propose the same 3 (dups) + 1 new -> only the new runs a campaign
    # r4(8,20)→canon(10,20): distinct from existing (0,10),(5,10),(5,20); fast=8<slow=20 ✓
    res2 = orchestrator.ingest_proposals(lib, [_cand("r1", 2, 9), _cand("r2", 3, 9),
                                               _cand("r3", 3, 20), _cand("r4", 8, 20)],
                                         "2026-07-01T00:00:00", cfg=DEFAULT_LAB_CONFIG,
                                         gate_cfg=DEFAULT_GATE_A_CONFIG, cost=NO_COST,
                                         get_bars=_bars, m=m)
    assert res1.m_after == 3 and res2.m_after == 4
    assert res2.m_after >= res1.m_after            # never resets / decreases


def test_killed_fingerprint_blocked_in_cooldown_then_unblocked(monkeypatch):
    _patch_screen(monkeypatch, pass_names={"k"})
    k = _cand("k", 10, 20)
    fp = orchestrator.dedup.fingerprint(k)
    killed = StrategyRecord(id=fp, strategy=k, fingerprint=fp, canon_bucket="cb",
                            archetype="trend_follow", cohort="trend_follow:up/low",
                            status="rejected", created_at_iso="2026-01-01", as_of="2026-01-01",
                            cooldown_until_iso="2026-12-31T00:00:00", lineage=ProposalRecord())

    blocked = orchestrator.ingest_proposals([killed], [k], "2026-06-29T00:00:00",
                                            cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
                                            cost=NO_COST, get_bars=_bars, m=5)
    assert blocked.accepted == [] and blocked.m_after == 5 and len(blocked.duplicates) == 1

    lib2 = [killed]
    revived = orchestrator.ingest_proposals(lib2, [k], "2027-01-02T00:00:00",
                                            cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
                                            cost=NO_COST, get_bars=_bars, m=5)
    assert revived.accepted == [fp] and revived.m_after == 6          # cooldown expired -> fresh campaign
    assert [r for r in lib2 if r.fingerprint == fp][0].status == "proving"


# ── Task 31: advance + reseed delegate ────────────────────────────────────────

from webull_api.lab import trial as trial_mod
from webull_api.lab import verdict as verdict_mod
from webull_api.lab.schema import (DEFAULT_GATE_B_CONFIG, ProvingState, RegimeTag, ReseedResult)


def _proving_rec(rec_id):
    s = _cand(rec_id)
    return StrategyRecord(id=rec_id, strategy=s, fingerprint=rec_id, canon_bucket=rec_id,
                          archetype="trend_follow", cohort="trend_follow:up/low",
                          status="proving", created_at_iso="2026-01-01", as_of="2026-01-01",
                          trial_id=f"t-{rec_id}", lineage=ProposalRecord())


def _state(rec_id):
    return ProvingState(trial_id=f"t-{rec_id}", strategy=_cand(rec_id), basket=list(["AAPL"]),
                        inception_et_date="2026-01-01", started_at_iso="2026-01-01")


def test_advance_respects_max_concurrent_proving(monkeypatch):
    seen = []
    monkeypatch.setattr(trial_mod, "advance",
                        lambda st, fresh, **k: (seen.append(st.trial_id) or (st, 1)))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    lib = [_proving_rec(f"r{i}") for i in range(5)]
    states = {r.trial_id: _state(r.id) for r in lib}
    cfg = LabConfig(max_concurrent_proving=3)
    res = orchestrator.advance(lib, cfg=cfg, gate_a=DEFAULT_GATE_A_CONFIG, gate_b=DEFAULT_GATE_B_CONFIG,
                               cost=NO_COST, get_bars=_bars, today_et="2026-06-29", now_iso="2026-06-29T00:00:00",
                               load_state=lambda tid: states[tid], save_state=lambda st: None, m=10)
    assert len(seen) == 3 and len(res.advanced) == 3 and res.no_op is False


def test_advance_no_op_when_no_bar_closed(monkeypatch):
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 0))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: "proving")
    lib = [_proving_rec("r0")]
    states = {"t-r0": _state("r0")}
    res = orchestrator.advance(lib, cfg=DEFAULT_LAB_CONFIG, gate_a=DEFAULT_GATE_A_CONFIG,
                               gate_b=DEFAULT_GATE_B_CONFIG, cost=NO_COST, get_bars=_bars,
                               today_et="2026-06-29", now_iso="2026-06-29T00:00:00",
                               load_state=lambda tid: states[tid], save_state=lambda st: None, m=1)
    assert res.no_op is True and res.advanced == []


def test_advance_promotes_and_kills(monkeypatch):
    verdicts = {"t-win": "confirmed_proven", "t-lose": "rejected"}
    monkeypatch.setattr(trial_mod, "advance", lambda st, fresh, **k: (st, 1))
    monkeypatch.setattr(verdict_mod, "evaluate_verdict", lambda st, **k: verdicts[st.trial_id])
    lib = [_proving_rec("win"), _proving_rec("lose")]
    states = {"t-win": _state("win"), "t-lose": _state("lose")}
    res = orchestrator.advance(lib, cfg=DEFAULT_LAB_CONFIG, gate_a=DEFAULT_GATE_A_CONFIG,
                               gate_b=DEFAULT_GATE_B_CONFIG, cost=NO_COST, get_bars=_bars,
                               today_et="2026-06-29", now_iso="2026-06-29T00:00:00",
                               load_state=lambda tid: states[tid], save_state=lambda st: None, m=1)
    assert res.promoted == ["win"] and res.killed == ["lose"]
    lose = [r for r in lib if r.id == "lose"][0]
    assert lose.kills == 1 and lose.cooldown_until_iso is not None and lose.status == "rejected"


def test_reseed_delegates_to_select_seeds():
    lib = [_rec_conf := StrategyRecord(
        id="c", strategy=_cand("c"), fingerprint="c", canon_bucket="c", archetype="trend_follow",
        cohort="trend_follow:up/low", status="confirmed_proven", created_at_iso="2026-01-01",
        as_of="2026-01-01", lineage=ProposalRecord())]
    res = orchestrator.reseed(lib, "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG,
                              regime_now=RegimeTag(trend="up", vol="low"))
    assert isinstance(res, ReseedResult) and [s.fingerprint for s in res.seeds] == ["c"]


# ── Task 2: max_admit cap + build_proposal_brief.avoid_canon_buckets ──────────

def test_ingest_max_admit_caps_accepted_and_m(monkeypatch):
    _patch_screen(monkeypatch, pass_names={"p1", "p2", "p3"})
    lib = []
    # p1(2,9)->canon(0,10), p2(3,9)->canon(5,10), p3(3,20)->canon(5,20): all distinct, all pass.
    # max_admit=2 breaks after the 2nd admit -> only 2 Gate-A campaigns run, p3 never screened.
    res = orchestrator.ingest_proposals(
        lib, [_cand("p1", 2, 9), _cand("p2", 3, 9), _cand("p3", 3, 20)],
        "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
        cost=NO_COST, get_bars=_bars, m=0, max_admit=2)
    assert len(res.accepted) == 2 and res.m_after == 2
    assert len([r for r in lib if r.status == "proving"]) == 2


def test_ingest_max_admit_none_is_byte_identical(monkeypatch):
    # default None == today's behavior: all 3 distinct passers admitted, M == 3
    _patch_screen(monkeypatch, pass_names={"p1", "p2", "p3"})
    lib = []
    res = orchestrator.ingest_proposals(
        lib, [_cand("p1", 2, 9), _cand("p2", 3, 9), _cand("p3", 3, 20)],
        "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG,
        cost=NO_COST, get_bars=_bars, m=0)
    assert len(res.accepted) == 3 and res.m_after == 3


def test_build_proposal_brief_includes_avoid_canon_buckets():
    lib = [StrategyRecord(
        id="x", strategy=_cand("x"), fingerprint="x", canon_bucket="up/low|sma_cross|0|10",
        archetype="trend_follow", cohort="trend_follow:up/low", status="proving",
        created_at_iso="2026-01-01", as_of="2026-01-01", lineage=ProposalRecord())]
    brief = orchestrator.build_proposal_brief(
        lib, "2026-06-29T00:00:00", regime_now=RegimeTag(trend="up", vol="low"),
        reseed_result=ReseedResult(), cfg=DEFAULT_LAB_CONFIG, tombstones=[])
    assert brief.avoid_canon_buckets == ["up/low|sma_cross|0|10"]


def test_ingest_writes_a_consistency_score_on_the_new_record(monkeypatch):
    """#3 latent bug (2026-07-26 audit): scoring.consistency_score — the documented SOLE ranking
    key — was imported by nothing in the production path, so StrategyRecord.score stayed None,
    lab_service ranking degenerated to unranked and reseed scored everything 0.0."""
    _patch_screen(monkeypatch, pass_names={"good1"})
    lib = []
    orchestrator.ingest_proposals(
        lib, [_cand("good1", 10, 20)], "2026-06-29T00:00:00", cfg=DEFAULT_LAB_CONFIG,
        gate_cfg=DEFAULT_GATE_A_CONFIG, cost=NO_COST, get_bars=_bars, m=0)
    rec = lib[0]
    assert rec.score is not None, "an accepted record must carry its ranking key"
    assert 0.0 <= rec.score.score <= 100.0
    # proving records are capped below the proving ceiling — a fresh record can never outrank
    # a Gate-B survivor on Gate-A numbers alone
    from webull_api.lab.schema import DEFAULT_SCORE_WEIGHTS
    assert rec.score.score <= DEFAULT_SCORE_WEIGHTS.proving_cap


def test_ingest_failed_entry_carries_gate_a_metrics(monkeypatch):
    # Decision-grade telemetry (2026-08-15): fail codes alone can't tell a hair's-width near-miss
    # from a mile-off reject, so the failed entry persists the report's raw metric values.
    def fake(strategy, bars_by_symbol, *, cfg, cost, m, require_cost_robust=False,
             lockbox_bars_by_symbol=None):
        return WalkForwardReport(passed=False, fail_codes=["dsr_below_floor"],
                                 regime_buckets=["up/low"], m_at_eval=m,
                                 dsr=0.912345678, expectancy_ci=(0.0123456789, 0.5),
                                 net_edge=0.223456789, edge_floor=0.1, breadth_frac=0.65,
                                 pooled_trades=140, max_drawdown_pct=-8.7654321)
    monkeypatch.setattr(gate_a, "screen_one", fake)
    res = orchestrator.ingest_proposals(
        [], [_cand("f1")], "2026-08-15T00:00:00",
        cfg=DEFAULT_LAB_CONFIG, gate_cfg=DEFAULT_GATE_A_CONFIG, cost=NO_COST, get_bars=_bars, m=0)
    entry = res.gate_a_failed[0]
    assert entry["fail_codes"] == ["dsr_below_floor"]
    assert entry["dsr"] == 0.912346 and entry["ci_lb"] == 0.012346          # 6dp
    assert entry["net_edge"] == 0.223457 and entry["edge_floor"] == 0.1
    assert entry["breadth_frac"] == 0.65 and entry["pooled_trades"] == 140  # 4dp / int
    assert entry["max_drawdown_pct"] == -8.7654
