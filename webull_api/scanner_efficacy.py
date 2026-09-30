"""Pure scanner-efficacy math (no I/O, never imports trading): forward-return scoring of
snapshot picks against SPY on daily bars, and the owner-facing summary. SPY's bar DATES are the
trading calendar; the symbol's bars are joined BY DATE (a missing session skips that window —
retried never, the symbol simply didn't trade the full window). Entry = next session's OPEN
after the snapshot date (no lookahead — picks are produced after the close); exit = CLOSE of
the w-th session counting the entry session as 1. excess_bp = (sym% − spy%) × 100."""
from __future__ import annotations

import statistics

WINDOWS = (5, 10, 20)


def score_snapshot(snapshot: dict, sym_bars: list[dict], spy_bars: list[dict],
                   windows=WINDOWS, *, scored_at: str = "") -> list[dict]:
    """Score every matured window for one snapshot; [] when nothing has matured."""
    date = str(snapshot.get("date", ""))
    spy_dates = [str(b.get("time", ""))[:10] for b in spy_bars]
    entry_idx = next((i for i, d in enumerate(spy_dates) if d > date), None)
    if entry_idx is None:
        return []
    sym_by_date = {str(b.get("time", ""))[:10]: b for b in sym_bars}
    out: list[dict] = []
    for w in windows:
        end = entry_idx + w - 1
        if end >= len(spy_bars):
            continue                       # window not matured yet
        d_in, d_out = spy_dates[entry_idx], spy_dates[end]
        sb_in, sb_out = sym_by_date.get(d_in), sym_by_date.get(d_out)
        if sb_in is None or sb_out is None:
            continue                       # symbol missing a session (halt/new listing)
        try:
            s_in, s_out = float(sb_in["open"]), float(sb_out["close"])
            p_in, p_out = float(spy_bars[entry_idx]["open"]), float(spy_bars[end]["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if s_in <= 0 or p_in <= 0:
            continue
        sym_ret = (s_out / s_in - 1) * 100.0
        spy_ret = (p_out / p_in - 1) * 100.0
        out.append({
            "id": f"{date}:{snapshot.get('symbol')}:{w}",
            "snapshot_id": snapshot.get("id"),
            "date": date, "symbol": snapshot.get("symbol"), "window": w,
            "entry_date": d_in,
            "entry_price": round(s_in, 4), "exit_price": round(s_out, 4),
            "sym_ret_pct": round(sym_ret, 4), "spy_ret_pct": round(spy_ret, 4),
            "excess_bp": round((sym_ret - spy_ret) * 100.0, 1),
            "scored_at": scored_at,
        })
    return out


def efficacy_summary(snapshots: list[dict], scores: list[dict], *, today: str = "") -> dict:
    """Per-window hit-rate/avg vs SPY, by-tag attribution (a multi-tag pick counts toward each
    tag, using its across-window mean excess), pending window count, today's snap count."""
    by_window: dict[int, list[float]] = {}
    for s in scores:
        try:
            by_window.setdefault(int(s["window"]), []).append(float(s["excess_bp"]))
        except (KeyError, TypeError, ValueError):
            continue
    windows = [{"window": w, "n": len(xs),
                "hit_rate": round(sum(1 for x in xs if x > 0) / len(xs), 3),
                "avg_excess_bp": round(sum(xs) / len(xs), 1),
                "median_excess_bp": round(statistics.median(xs), 1)}
               for w, xs in sorted(by_window.items())]

    ex_by_snap: dict[str, list[float]] = {}
    for s in scores:
        try:
            ex_by_snap.setdefault(str(s.get("snapshot_id")), []).append(float(s["excess_bp"]))
        except (KeyError, TypeError, ValueError):
            continue
    by_tag: dict[str, dict] = {}
    for snap in snapshots:
        xs = ex_by_snap.get(str(snap.get("id")))
        if not xs:
            continue
        avg = sum(xs) / len(xs)
        for tag in snap.get("tags") or []:
            b = by_tag.setdefault(str(tag), {"n": 0, "_sum": 0.0, "_wins": 0})
            b["n"] += 1
            b["_sum"] += avg
            b["_wins"] += 1 if avg > 0 else 0
    tags = [{"tag": t, "n": b["n"], "hit_rate": round(b["_wins"] / b["n"], 3),
             "avg_excess_bp": round(b["_sum"] / b["n"], 1)}
            for t, b in sorted(by_tag.items(), key=lambda kv: (-kv[1]["n"], kv[0]))]

    score_ids = {str(s.get("id")) for s in scores}
    pending = sum(1 for snap in snapshots for w in WINDOWS
                  if f"{snap.get('date')}:{snap.get('symbol')}:{w}" not in score_ids)
    return {"windows": windows, "by_tag": tags, "pending": pending,
            "snapped_today": sum(1 for s in snapshots if str(s.get("date")) == today),
            "snapshots_total": len(snapshots), "scores_total": len(scores)}
