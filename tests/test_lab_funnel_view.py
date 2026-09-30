"""lab_service's read views are disk-read-only composition: the funnel (cycles.jsonl + proven shelf), the proven
shelf, one candidate."""
import pytest

from webull_api.lab.schema import PaperProvenRecord, WalkForwardReport
from webull_api.strategy.schema import Strategy
from webull_web import lab_service, lab_store

CYC = [{"cycle_seq": 1, "cycle_date": "2026-07-10", "generated": 4, "screened": 4,
        "duplicates": 0, "accepted": ["a"], "new_proving": ["a"], "promoted": [], "killed": [],
        "gate_a_failed": [{"fingerprint": "f", "fail_codes": ["ci_lb_negative"]}]}]


def test_funnel_view_empty_store(monkeypatch):
    monkeypatch.setattr(lab_service.lab_store, "read_cycles", lambda limit=20: [])
    monkeypatch.setattr(lab_service.lab_store, "load_proven", lambda: [])
    out = lab_service.funnel_view()
    assert out["latest"] is None and out["lifetime"]["cycles_run"] == 0


def test_funnel_view_counts_proven(monkeypatch):
    monkeypatch.setattr(lab_service.lab_store, "read_cycles", lambda limit=20: list(CYC))
    monkeypatch.setattr(lab_service.lab_store, "load_proven", lambda: [object()])
    out = lab_service.funnel_view()
    assert out["lifetime"]["proven"] == 1 and out["latest"]["accepted"] == 1
    assert out["fail_codes"][0]["code"] == "ci_lb_negative"


def test_funnel_view_survives_unreadable_proven_shelf(monkeypatch):
    monkeypatch.setattr(lab_service.lab_store, "read_cycles", lambda limit=20: list(CYC))
    def boom():
        raise RuntimeError("proven store unreadable")
    monkeypatch.setattr(lab_service.lab_store, "load_proven", boom)
    assert lab_service.funnel_view()["lifetime"]["proven"] == 0   # degrade, never 500


# Ported 2026-09-28 from the archived GET /api/lab/proven and /api/lab/candidate route tests (their only coverage).
def _strat():
    return Strategy(name="s1", symbol="AAPL",
                    entry={"type": "sma_cross", "fast": 20, "slow": 50, "direction": "above"})


def test_proven_view_lists_the_shelf(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path / "lab"))
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path / "journal"))
    lab_store.save_proven(PaperProvenRecord(graduated_at="2026-06-29T00:00:00Z", strategy=_strat(),
                                            gate_a=WalkForwardReport(), gate_b={}, assumptions={}, objective_met={}))
    out = lab_service.proven_view()
    assert len(out) == 1 and out[0]["verdict"] == "confirmed_proven"


def test_candidate_detail_of_an_unknown_id_is_not_found(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_DIR", str(tmp_path / "lab"))
    lab_store.save_library([])
    with pytest.raises(FileNotFoundError):
        lab_service.candidate_detail("nope")
