import json
import pytest
from webull_api.strategy.schema import Strategy
from webull_api.lab import schema as ls
from webull_web import lab_store


@pytest.fixture(autouse=True)
def _lab_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path))
    return tmp_path


def _strat():
    return Strategy(name="s", symbol="SPY",
                    entry={"type": "sma_cross", "fast": 10, "slow": 20, "direction": "above"})


def _rec(rec_id="rec1", **kw):
    base = dict(id=rec_id, strategy=_strat(), fingerprint="fp" + rec_id, canon_bucket="cb",
                archetype="trend_follow", cohort="trend_follow:up/low",
                created_at_iso="2026-06-29T00:00:00", as_of="2026-06-29")
    base.update(kw)
    return ls.StrategyRecord(**base)


def test_library_round_trip_and_get_record():
    lab_store.save_library([_rec("rec1"), _rec("rec2")])
    lib = lab_store.load_library()
    assert {r.id for r in lib} == {"rec1", "rec2"}
    assert lab_store.get_record("rec1").fingerprint == "fprec1"
    with pytest.raises(FileNotFoundError):
        lab_store.get_record("missing")


def test_upsert_record_replaces_in_place():
    lab_store.save_library([_rec("rec1", status="proving")])
    lab_store.upsert_record(_rec("rec1", status="rejected"))
    lib = lab_store.load_library()
    assert len(lib) == 1 and lib[0].status == "rejected"


def test_trial_round_trip_and_resume():
    st = ls.ProvingState(trial_id="t1", strategy=_strat(), basket=["SPY"],
                         bars_by_symbol={"SPY": [{"time": "t0", "close": 1.0}]},
                         inception_et_date="2026-06-29", started_at_iso="2026-06-29T00:00:00")
    lab_store.save_trial(st)
    again = lab_store.load_trial("t1")
    assert again.status == "proving" and again.bars_by_symbol["SPY"][0]["close"] == 1.0
    with pytest.raises(FileNotFoundError):
        lab_store.load_trial("nope")


def test_m_counter_is_monotone_and_persists(_lab_dir):
    assert lab_store.read_meta()["M"] == 0
    assert lab_store.bump_m() == 1
    assert lab_store.bump_m(2) == 3
    assert lab_store.read_meta()["M"] == 3
    # persists across a fresh read (new "process")
    assert lab_store.read_meta()["M"] == 3


def test_library_corruption_is_quarantined_not_fatal(_lab_dir):
    (_lab_dir / "library.json").write_text("{ not json", encoding="utf-8")
    assert lab_store.load_library() == []
    assert (_lab_dir / "library.json.corrupt").exists()


def test_meta_corruption_with_no_cycles_is_cold_start(_lab_dir):
    (_lab_dir / "meta.json").write_text("garbage", encoding="utf-8")
    # no cycles.jsonl to rebuild from -> a genuine cold start (M=0); the bad file is quarantined.
    assert lab_store.read_meta()["M"] == 0
    assert (_lab_dir / "meta.json.corrupt").exists()


def test_proven_round_trip():
    rec = ls.PaperProvenRecord(graduated_at="2026-06-29T00:00:00", strategy=_strat(),
                               gate_a=ls.WalkForwardReport(), gate_b={"trial_id": "t1"},
                               assumptions={"note": "UPPER BOUND"},
                               objective_met={"profitable_window_frac": 0.7})
    lab_store.save_proven(rec)
    proven = lab_store.load_proven()
    assert len(proven) == 1 and proven[0].human_promotion_authorized is False


def test_events_and_tombstones(_lab_dir):
    lab_store.append_event({"type": "promote", "id": "rec1"})
    lab_store.append_event({"type": "gate_a_failed", "fingerprint": "fpX", "fail_codes": ["dsr_below_floor"]})
    tombs = lab_store.read_tombstones()
    assert len(tombs) == 1 and tombs[0]["fingerprint"] == "fpX"


def _cycle(seq=1, m_before=0, m_after=0):
    return ls.CycleReport(
        cycle_seq=seq, cycle_date="2026-06-30", ran_at_iso="2026-06-30T13:00:00",
        regime=ls.RegimeTag(trend="up", vol="low"),
        decision=ls.BatchDecision(active_trials=0, admitted_waiting=0, open_slots=0,
                                  pass_rate_est=0.25, mutation_n=0, explore_n=0, max_admit=0),
        m_before=m_before, m_after=m_after, stagnation=ls.StagnationState())


def test_append_read_cycles_roundtrip(_lab_dir):
    lab_store.append_cycle(_cycle(seq=1, m_before=0, m_after=2))
    lab_store.append_cycle(_cycle(seq=2, m_before=2, m_after=5))
    cycles = lab_store.read_cycles()
    assert [c["cycle_seq"] for c in cycles] == [1, 2]       # newest-last order preserved
    assert cycles[-1]["m_after"] == 5
    assert lab_store.read_cycles(limit=1) == [cycles[-1]]   # only the last `limit`


def test_read_cycles_skips_corrupt_lines(_lab_dir):
    lab_store.append_cycle(_cycle(seq=1))
    with (_lab_dir / "cycles.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("{ not json\n")
    lab_store.append_cycle(_cycle(seq=3))
    cycles = lab_store.read_cycles()
    assert [c["cycle_seq"] for c in cycles] == [1, 3]       # corrupt middle line dropped


def test_read_cycles_missing_file_is_empty(_lab_dir):
    assert lab_store.read_cycles() == []


def test_meta_backfills_new_cycle_defaults(_lab_dir):
    # an OLD meta written before the autopilot fields still loads, new keys defaulted
    (_lab_dir / "meta.json").write_text(json.dumps({"last_advance_date": "2026-06-01", "M": 7}),
                                        encoding="utf-8")
    meta = lab_store.read_meta()
    assert meta["M"] == 7 and meta["last_advance_date"] == "2026-06-01"
    assert meta["last_cycle_date"] == "" and meta["cycle_seq"] == 0
    assert meta["last_cycle"] is None and meta["stagnation"] == {}


def test_meta_corruption_reconstructs_M_from_cycles(_lab_dir):
    # #2: a corrupt meta must NOT silently reset M to 0 (that would discard the accumulated
    # multiple-testing correction + defeat the same-day guard). M + last_cycle_date are rebuilt
    # from the append-only cycles.jsonl, and the corrupt file is quarantined.
    lab_store.append_cycle(_cycle(seq=1, m_before=0, m_after=40))
    lab_store.append_cycle(_cycle(seq=2, m_before=40, m_after=64))
    (_lab_dir / "meta.json").write_text("{ corrupt", encoding="utf-8")
    meta = lab_store.read_meta()
    assert meta["M"] == 64                                   # max m_after, NOT 0
    assert meta["last_cycle_date"] == "2026-06-30"           # same-day guard restored
    assert meta["cycle_seq"] == 2
    assert (_lab_dir / "meta.json.corrupt").exists()


def test_meta_reconstruction_tolerates_malformed_cycle_fields(_lab_dir):
    # #2 recovery robustness: a valid-JSON cycles line with a non-int m_after/cycle_seq must NOT
    # crash the recovery path itself (that would defeat the graceful-recovery intent) — coerced to 0.
    lab_store.append_cycle(_cycle(seq=1, m_before=0, m_after=30))
    with (_lab_dir / "cycles.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"cycle_seq": "oops", "m_after": None, "cycle_date": "2026-07-01"}) + "\n")
    (_lab_dir / "meta.json").write_text("{ corrupt", encoding="utf-8")
    meta = lab_store.read_meta()          # must not raise
    assert meta["M"] == 30                # max of (30, coerced-0) — the good value wins
    assert meta["cycle_seq"] == 0         # non-int "oops" coerced, not a crash
