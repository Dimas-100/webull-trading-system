"""The sizing and stacking study (spec docs/superpowers/specs/2026-09-10-sizing-stacking-study-design.md).

Read-only research: it replays two already-produced trade lists (the RSI(2) swing book and the IBS ETF
book) through one book simulator at each declared fraction of equity per lot, stacked and not, and
writes docs/reviews/<today>-sizing-study-<window>.{json,md}. Nothing here touches a live book.

    .venv/Scripts/python.exe scripts/sizing_study.py --window develop --tag size1
    .venv/Scripts/python.exe scripts/sizing_study.py --window confirm --tag size1   # controller only

The confirm window is the study's one holdout opening: this script refuses (exit 2) unless the latest
develop report carrying the same tag names a selected cell, and it then runs THAT cell only, plus the
B0 and SPY references.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_api.paths import REPO_ROOT                                  # noqa: E402
from webull_api.sizing_study import study                               # noqa: E402
from webull_api.sizing_study.cells import B0, SPY                       # noqa: E402
from webull_api.tiingo import store                                     # noqa: E402

ET = ZoneInfo("America/New_York")


def _stem(window: str) -> str:
    """Spec §8 fixes the report names, so the tag rides in the payload (and gates the confirm
    opening) instead of in the filename -- the one place this diverges from backtest_ibs_book.py."""
    return f"sizing-study-{window}"


def _latest_json(out_dir: Path, window: str) -> Path | None:
    files = sorted(out_dir.glob(f"*-{_stem(window)}.json"))
    return files[-1] if files else None


def _develop_selection(out_dir: Path, tag: str) -> tuple[dict | None, str]:
    """The tagged develop report's selection, or why the confirm window stays shut."""
    p = _latest_json(out_dir, "develop")
    if p is None:
        return None, "no develop report on record"
    payload = json.loads(p.read_text(encoding="utf-8"))
    if (payload.get("tag") or "") != (tag or ""):
        return None, f"develop report {p.name} carries tag {payload.get('tag')!r}, not {tag!r}"
    sel = (payload.get("selection") or {}).get("selected")
    if not sel:
        return None, f"develop report {p.name} names no selected cell"
    return {"payload": payload, "selected": sel, "path": str(p)}, ""


def _f(v, nd=2):
    return "—" if v is None else (f"{v:.{nd}f}" if isinstance(v, float) else str(v))


def _cell_table(payload: dict) -> list[str]:
    rows = ["| cell | books | f | N | CAGR % | marked max DD % | Calmar | worst year | months + % | "
            "utilisation % | taken | skipped (slots/cash) | RSI2–IBS monthly corr |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for cid in payload["cells_run"]:
        m = payload["cells"][cid]
        f = m.get("f")
        sk = m.get("trades_skipped", {})
        rows.append(
            f"| {cid} | {'+'.join(m.get('books') or ([SPY] if cid == SPY else []))} | "
            f"{('1/' + str(round(1 / f))) if f else ('$%.0f lots' % m['fixed_size'] if m.get('fixed_size') else '—')} | "
            f"{m.get('slots') or '—'} | {_f(m.get('cagr_pct'))} | {_f(m.get('max_drawdown_pct'))} | "
            f"{_f(m.get('calmar'), 3)} | {m.get('worst_year')} {_f(m.get('worst_year_pct'))} % | "
            f"{_f(m.get('months_positive_pct'), 1)} | {_f(m.get('mean_utilisation_pct'), 1)} | "
            f"{m.get('trades_taken')} | {sk.get('slots', 0)}/{sk.get('cash', 0)} | "
            f"{_f(m.get('book_monthly_correlation'), 3)} |")
    return rows


def _markdown(payload: dict) -> str:
    w = payload["window"]
    out = [f"# Sizing and stacking study — {w} window ({payload['start']} → {payload['end']})", "",
           f"Generated {payload['generated']} · spec `{payload['spec']}` · tag `{payload['tag']}` · "
           f"sessions {payload['sessions']} · opening equity ${payload['start_equity']:,.0f} · "
           f"signals in window {payload['signals']['in_window']} of {payload['signals']['total']}. "
           f"**Every number below is {w}.**", "",
           "Marked daily at adjusted closes; a lot is a dollar size (fractional shares); "
           "settle → enter → mark, in that order.", ""]
    out += _cell_table(payload)
    sel = payload.get("selection")
    if sel:
        out += ["", "## Selection (§6, stated before the run)", "",
                f"**Selected: {sel['selected'] or 'NONE'}** — {sel['reason']}.", "",
                "| cell | Calmar | marked max DD % | CAGR % | within the "
                f"{sel['dd_budget_pct']:g}% budget |", "|---|---|---|---|---|"]
        for r in sel["ranking"]:
            out.append(f"| {r['cell']} | {_f(r['calmar'], 3)} | {_f(r['max_drawdown_pct'])} | "
                       f"{_f(r['cagr_pct'])} | {'yes' if r['within_budget'] else 'no'} |")
    if payload.get("verdict"):
        v = payload["verdict"]
        out += ["", "## Confirm verdict (§6 pass conditions)", "",
                f"**{v['verdict']}** — failing: {', '.join(v['failing']) or 'none'}."]
    out += ["", "## Per year (%, from the marked equity series)", ""]
    years = sorted({y for cid in payload["cells_run"] for y in payload["cells"][cid]["yearly_returns_pct"]})
    out += ["| cell | " + " | ".join(years) + " |", "|---|" + "---|" * len(years)]
    for cid in payload["cells_run"]:
        ys = payload["cells"][cid]["yearly_returns_pct"]
        out.append(f"| {cid} | " + " | ".join(_f(ys.get(y)) for y in years) + " |")
    out += ["", "## Per cell", ""]
    for cid in payload["cells_run"]:
        m = payload["cells"][cid]
        dd, notes = m["drawdown"], m["data_notes"]
        out += [f"### {cid} — {m['label']}", "",
                f"- equity {m['start_equity']:,.0f} → {m['end_equity']:,.2f} "
                f"({_f(m['total_return_pct'])} % total, CAGR {_f(m['cagr_pct'])} %)",
                f"- marked max DD {_f(m['max_drawdown_pct'])} % — peak {dd['peak_date'] or '(opening)'} "
                f"${_f(dd['peak_equity'])} → trough {dd['trough_date']} ${_f(dd['trough_equity'])}; "
                f"Calmar {_f(m['calmar'], 3)}",
                f"- worst year {m['worst_year']} {_f(m['worst_year_pct'])} % · months positive "
                f"{m['months_positive']}/{m['months']} ({_f(m['months_positive_pct'], 1)} %) · "
                f"mean utilisation {_f(m['mean_utilisation_pct'], 1)} %",
                f"- trades taken {m['trades_taken']} {json.dumps(m['trades_taken_by_book'])} · "
                f"skipped {json.dumps(m['trades_skipped'])} · max open {m['max_open']} · "
                f"open at end {m['open_at_end']}",
                f"- data notes: `{json.dumps(notes)}`", ""]
    return "\n".join(out) + "\n"


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--window", choices=sorted(study.WINDOWS), required=True)
    ap.add_argument("--tag", default="", help="carried in the payload; the confirm guard demands a match")
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "docs" / "reviews"))
    ap.add_argument("--today", default=None, help="override the ET date used in the report filename")
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    cell_ids = None
    develop = None
    if a.window == "confirm":
        develop, why = _develop_selection(out_dir, a.tag)
        if develop is None:
            print(f"refused: {why} — the confirm window opens only for a selected cell (§6)")
            return 2
        cell_ids = [develop["selected"]] + ([B0] if develop["selected"] != B0 else [])
        print(f"confirm opening for {develop['selected']} (from {develop['path']}) plus {B0} and {SPY}")

    payload = study.run_window(a.window, read=store.read, tag=a.tag, cell_ids=cell_ids)
    if a.window == "confirm":
        d_cells = develop["payload"]["cells"]
        payload["develop_report"] = develop["path"]
        payload["verdict"] = study.confirm_verdict(d_cells[develop["selected"]],
                                                   payload["cells"][develop["selected"]],
                                                   payload["cells"][B0])

    for cid in payload["cells_run"]:
        m = payload["cells"][cid]
        print(f"{cid}: CAGR {_f(m['cagr_pct'])}% maxDD {_f(m['max_drawdown_pct'])}% "
              f"Calmar {_f(m['calmar'], 3)} worst {m['worst_year']} {_f(m['worst_year_pct'])}% "
              f"util {_f(m['mean_utilisation_pct'], 1)}% taken {m['trades_taken']} "
              f"skipped {m['trades_skipped_total']}")
    if payload.get("selection"):
        print(f"selected: {payload['selection']['selected']} — {payload['selection']['reason']}")
    if payload.get("verdict"):
        print(f"verdict: {payload['verdict']['verdict']} {payload['verdict']['failing']}")

    today = a.today or datetime.now(ET).date().isoformat()
    stem = f"{today}-{_stem(a.window)}"
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(_markdown(payload), encoding="utf-8")
    print(f"wrote {out_dir / (stem + '.md')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
