"""The §6 selection rule, exactly as pre-registered: highest develop Calmar among cells whose marked
max drawdown is <= 30%; if no cell clears 30%, the study reports and stops."""
from __future__ import annotations

from webull_api.sizing_study.cells import DD_BUDGET_PCT, SELECTABLE
from webull_api.sizing_study.study import select


def _cells(**by_id) -> dict:
    return {cid: {"calmar": cal, "max_drawdown_pct": dd, "cagr_pct": dd * cal if cal else 0.0}
            for cid, (cal, dd) in by_id.items()}


def test_highest_calmar_within_the_budget_wins():
    cells = _cells(R8=(0.40, 12.0), R6=(0.55, 18.0), R4=(0.60, 25.0), S8=(0.50, 10.0),
                   S6=(0.52, 15.0), S4=(0.45, 28.0), I4=(0.30, 20.0))
    out = select(cells)
    assert out["selected"] == "R4" and out["dd_budget_pct"] == DD_BUDGET_PCT
    assert set(out["eligible"]) == set(SELECTABLE)
    assert out["ranking"][0]["cell"] == "R4" and "highest develop Calmar" in out["reason"]


def test_a_bigger_calmar_outside_the_budget_is_not_eligible():
    cells = _cells(R8=(0.40, 12.0), R6=(0.55, 18.0), R4=(0.90, 34.0), S8=(0.50, 10.0),
                   S6=(0.52, 15.0), S4=(0.45, 31.0), I4=(0.30, 20.0))
    out = select(cells)
    assert out["selected"] == "R6"
    assert "R4" not in out["eligible"] and "S4" not in out["eligible"]
    assert out["ranking"][0]["cell"] == "R4" and out["ranking"][0]["within_budget"] is False


def test_no_cell_clears_the_budget_and_the_study_stops():
    cells = _cells(R8=(0.40, 32.0), R6=(0.55, 33.0), R4=(0.90, 34.0), S8=(0.50, 40.0),
                   S6=(0.52, 35.0), S4=(0.45, 31.0), I4=(0.30, 37.5))
    out = select(cells)
    assert out["selected"] is None and out["eligible"] == []
    assert "reports and stops" in out["reason"]


def test_references_never_compete_and_a_tie_falls_to_the_declared_order():
    cells = _cells(R8=(0.50, 10.0), R6=(0.50, 10.0), R4=(0.10, 10.0), S8=(0.10, 10.0),
                   S6=(0.10, 10.0), S4=(0.10, 10.0), I4=(0.10, 10.0))
    cells["B0"] = {"calmar": 9.9, "max_drawdown_pct": 1.0, "cagr_pct": 9.9}
    cells["SPY"] = {"calmar": 9.9, "max_drawdown_pct": 1.0, "cagr_pct": 9.9}
    out = select(cells)
    assert out["selected"] == "R8"                    # R8 is declared before R6 in §4
    assert all(r["cell"] in SELECTABLE for r in out["ranking"])


def test_a_cell_with_no_drawdown_or_no_calmar_is_not_selected():
    cells = {"R8": {"calmar": None, "max_drawdown_pct": 0.0, "cagr_pct": 5.0},
             "R6": {"calmar": 0.2, "max_drawdown_pct": 20.0, "cagr_pct": 4.0}}
    out = select(cells)
    assert out["selected"] == "R6"
