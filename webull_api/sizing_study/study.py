"""Run the declared cells over one window and apply the §6 selection rule.

Windows are fixed by §6: develop 2006-01-01 -> 2020-12-31, confirm 2021-01-01 -> 2024-12-31. The
develop window is read once, every cell; the confirm window is opened once, for the selected cell only
(plus the B0 and SPY references), and only by way of a develop report that names a selection.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from webull_api.sizing_study import book, metrics
from webull_api.sizing_study.cells import (B0, CELLS, DD_BUDGET_PCT, ORDER, SELECTABLE, SPY,
                                           START_EQUITY)
from webull_api.sizing_study.loaders import (CALENDAR_SYMBOL, Signal, by_entry_date, calendar_from,
                                             load_marks, load_signals)

DEVELOP = ("2006-01-01", "2020-12-31")
CONFIRM = ("2021-01-01", "2024-12-31")
WINDOWS = {"develop": DEVELOP, "confirm": CONFIRM}
ET = ZoneInfo("America/New_York")


def build_context(window: str, signals: list[Signal], read) -> dict:
    """The calendar, the dated signal stream and the marks for one window."""
    start, end = WINDOWS[window]
    calendar = calendar_from(read(CALENDAR_SYMBOL), start, end)
    symbols = {s.symbol for s in signals} | {CALENDAR_SYMBOL}
    marks = load_marks(symbols, calendar, read)
    return {"window": window, "start": start, "end": end, "calendar": calendar,
            "signals_by_date": by_entry_date(signals), "marks": marks, "signals": signals}


def run_cell(cell_id: str, ctx: dict, start_equity: float = START_EQUITY) -> dict:
    if cell_id == SPY:
        res = book.spy_equity(ctx["calendar"], ctx["marks"], CALENDAR_SYMBOL, start_equity)
        out = metrics.summarize(res)
        out["cell"] = SPY
    else:
        cell = CELLS[cell_id]
        res = book.run(cell, ctx["calendar"], ctx["signals_by_date"], ctx["marks"], start_equity)
        out = metrics.summarize(res)
        out["f"] = cell.f
        out["slots"] = cell.slots
        out["books"] = list(cell.books)
        out["fixed_size"] = cell.fixed_size
        out["cash_floor"] = cell.cash_floor
    out["label"] = CELLS[cell_id].label if cell_id in CELLS else "SPY reference · buy and hold"
    out["reference"] = cell_id in (B0, SPY)
    return out


def select(cells: dict[str, dict], dd_budget_pct: float = DD_BUDGET_PCT) -> dict:
    """§6: the cell with the highest develop Calmar among cells whose develop marked max drawdown is
    <= the budget. References (B0, SPY) never compete. If no cell clears the budget the study reports
    and stops. Ties (none expected) fall to the order the cells were declared in §4."""
    ranking = []
    for cid in SELECTABLE:
        m = cells.get(cid)
        if m is None:
            continue
        ranking.append({"cell": cid, "calmar": m.get("calmar"),
                        "max_drawdown_pct": m.get("max_drawdown_pct"),
                        "cagr_pct": m.get("cagr_pct"),
                        "within_budget": m.get("max_drawdown_pct") is not None
                        and m["max_drawdown_pct"] <= dd_budget_pct})
    eligible = [r for r in ranking if r["within_budget"] and r["calmar"] is not None]
    ranking.sort(key=lambda r: (r["calmar"] is None, -(r["calmar"] or 0.0), SELECTABLE.index(r["cell"])))
    if not eligible:
        return {"dd_budget_pct": dd_budget_pct, "selected": None, "eligible": [],
                "reason": f"no cell's marked max drawdown is <= {dd_budget_pct:g}% -- "
                          "the study reports and stops (§6)",
                "ranking": ranking}
    best = min(eligible, key=lambda r: (-r["calmar"], SELECTABLE.index(r["cell"])))
    return {"dd_budget_pct": dd_budget_pct, "selected": best["cell"],
            "eligible": [r["cell"] for r in eligible],
            "reason": f"highest develop Calmar ({best['calmar']:.4g}) among the "
                      f"{len(eligible)} cell(s) whose marked max drawdown is <= {dd_budget_pct:g}% "
                      f"(its own is {best['max_drawdown_pct']:.2f}%)",
            "ranking": ranking}


def run_window(window: str, *, read, tag: str = "", cell_ids: list[str] | None = None,
               signals: list[Signal] | None = None, start_equity: float = START_EQUITY,
               generated: str | None = None) -> dict:
    sigs = load_signals() if signals is None else signals
    ctx = build_context(window, sigs, read)
    ids = list(cell_ids) if cell_ids else list(ORDER)
    cells: dict[str, dict] = {}
    for cid in ids:
        cells[cid] = run_cell(cid, ctx, start_equity)
    cells[SPY] = run_cell(SPY, ctx, start_equity)
    payload = {
        "generated": generated or datetime.now(ET).isoformat(timespec="seconds"),
        "spec": "docs/superpowers/specs/2026-09-10-sizing-stacking-study-design.md",
        "window": window, "start": ctx["start"], "end": ctx["end"],
        "sessions": len(ctx["calendar"]), "tag": tag, "start_equity": start_equity,
        "cells_run": ids + [SPY],
        "signals": {"total": len(sigs),
                    "in_window": sum(1 for s in sigs if ctx["start"] <= s.entry_date <= ctx["end"])},
        "cells": cells,
    }
    if window == "develop":
        payload["selection"] = select(cells)
    return payload


def confirm_verdict(develop_cell: dict, confirm_cell: dict, b0_confirm: dict) -> dict:
    """§6 pass conditions, evaluated only when the controller opens the confirm window."""
    failing = []
    cg, dd, cal = confirm_cell.get("cagr_pct"), confirm_cell.get("max_drawdown_pct"), confirm_cell.get("calmar")
    d_dd, d_cal = develop_cell.get("max_drawdown_pct"), develop_cell.get("calmar")
    b0 = b0_confirm.get("cagr_pct")
    if cg is None or cg <= 0:
        failing.append("cagr<=0")
    if dd is None or d_dd is None or dd > 1.25 * d_dd:
        failing.append("max_dd>1.25x_develop")
    if cal is None or d_cal is None or cal < 0.5 * d_cal:
        failing.append("calmar<0.5x_develop")
    if cg is None or b0 is None or cg < b0:
        failing.append("cagr<B0_confirm")
    return {"verdict": "PASS" if not failing else "FAIL", "failing": failing}
