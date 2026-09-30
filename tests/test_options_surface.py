from datetime import date as D

import pytest

from webull_api import options_surface as osf
from webull_api.options_chain import build_occ


def _row(strike, civ, piv):
    return {"strike": strike, "call": {"imp_vol": civ}, "put": {"imp_vol": piv}}


# ── Task 1: pure skew / term_structure / liquidity ───────────────────────────────

def test_skew_put_skew_has_negative_slope():
    rows = [_row(90, "0.30", "0.40"), _row(100, "0.25", "0.30"), _row(110, "0.22", "0.26")]
    s = osf.skew(rows, 100.0)
    assert s["slope"] < 0
    assert s["atm_iv"] == pytest.approx(0.275, abs=1e-9)
    assert s["put_call_iv_spread"] is not None and s["put_call_iv_spread"] > 0
    assert len(s["points"]) == 3


def test_skew_empty_is_safe():
    assert osf.skew([], 100.0)["slope"] is None


def test_term_structure_shapes():
    contango = osf.term_structure([{"expiration": "2026-07-01", "dte": 7, "atm_iv": 0.20},
                                   {"expiration": "2026-09-01", "dte": 70, "atm_iv": 0.28}])
    assert contango["shape"] == "contango"
    back = osf.term_structure([{"expiration": "2026-07-01", "dte": 7, "atm_iv": 0.40},
                               {"expiration": "2026-09-01", "dte": 70, "atm_iv": 0.25}])
    assert back["shape"] == "backwardation"
    flat = osf.term_structure([{"expiration": "2026-07-01", "dte": 7, "atm_iv": 0.25},
                               {"expiration": "2026-09-01", "dte": 70, "atm_iv": 0.252}])
    assert flat["shape"] == "flat"


def test_term_structure_under_two_points_is_unknown_not_flat():
    # < 2 points is missing data, not a measurement — never presented as "flat".
    assert osf.term_structure([])["shape"] == "unknown"
    assert osf.term_structure(None)["shape"] == "unknown"
    one = osf.term_structure([{"expiration": "2026-07-01", "dte": 7, "atm_iv": 0.25}])
    assert one["shape"] == "unknown" and one["front_iv"] is None


def test_liquidity_good_fair_poor():
    good = osf.liquidity({"bid": "2.00", "ask": "2.04", "open_interest": "5000", "volume": "1200"})
    assert good["label"] == "good" and good["score"] >= 80
    poor = osf.liquidity({"bid": "1.00", "ask": "1.80", "open_interest": "3", "volume": "0"})
    assert poor["label"] == "poor" and poor["score"] <= 40
    assert osf.liquidity({"bid": None, "ask": None})["label"] == "unknown"


# ── Task 2: vol_surface_for orchestrator (injected; no network) ───────────────────

def _snap(rows):
    table = {r["symbol"]: r for r in rows}

    def fn(csv):
        return [table[s] for s in csv.split(",") if s in table]

    return fn


def test_vol_surface_for_combines_skew_and_term():
    e1, e2 = D(2026, 7, 17), D(2026, 9, 18)
    chain = {"rows": [{"strike": 95, "call": {"imp_vol": "0.26"}, "put": {"imp_vol": "0.34"}},
                      {"strike": 100, "call": {"imp_vol": "0.24"}, "put": {"imp_vol": "0.30"}},
                      {"strike": 105, "call": {"imp_vol": "0.22"}, "put": {"imp_vol": "0.27"}}],
             "spot": 100.0}
    atm_rows = [{"symbol": build_occ("AAPL", e1, "C", 100), "imp_vol": "0.24"},
                {"symbol": build_occ("AAPL", e2, "C", 100), "imp_vol": "0.30"}]
    vs = osf.vol_surface_for("AAPL", today=D(2026, 4, 18), spot_fn=lambda s: 100.0,
                             snapshot_fn=_snap(atm_rows), chain_fn=lambda *a, **k: chain,
                             exp_fn=lambda *a, **k: [e1.isoformat(), e2.isoformat()])
    assert vs["skew"]["slope"] < 0
    assert vs["term_structure"]["shape"] == "contango"
    assert vs["spot"] == 100.0


def test_vol_surface_term_probes_prune_and_retry_invalid_symbols():
    # The snapshot API is all-or-nothing: one off-grid ATM probe 417s the whole batch.
    # The term-structure probe must prune the invalids and retry the survivors (the old
    # blanket `except: pass` silently emptied the term structure instead).
    from webull_api.options import InvalidOptionSymbolError

    e1, e2, e3 = D(2026, 7, 17), D(2026, 8, 21), D(2026, 9, 18)
    bad = build_occ("AAPL", e2, "C", 100)   # this expiration's ATM probe is off-grid
    table = {build_occ("AAPL", e1, "C", 100): {"symbol": build_occ("AAPL", e1, "C", 100), "imp_vol": "0.24"},
             build_occ("AAPL", e3, "C", 100): {"symbol": build_occ("AAPL", e3, "C", 100), "imp_vol": "0.30"}}

    def snap(csv):
        syms = csv.split(",")
        invalid = {s for s in syms if s == bad}
        if invalid:
            raise InvalidOptionSymbolError(invalid)
        return [table[s] for s in syms if s in table]

    vs = osf.vol_surface_for("AAPL", today=D(2026, 4, 18), spot_fn=lambda s: 100.0,
                             snapshot_fn=snap, chain_fn=lambda *a, **k: {"rows": []},
                             exp_fn=lambda *a, **k: [e1.isoformat(), e2.isoformat(), e3.isoformat()])
    ts = vs["term_structure"]
    assert [p["expiration"] for p in ts["points"]] == [e1.isoformat(), e3.isoformat()]
    assert ts["shape"] == "contango"
