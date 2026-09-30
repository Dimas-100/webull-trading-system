"""The autopilot's own phone push (owner-approved 2026-09-21): a short plain-text note after any
run that PLACED a real order or hit an error. A silent run sends nothing -- the evening
manager's note carries the day's counts. Pure composition; run.py sends the result through
webull_api.push (best-effort, after placement, never on the gate path)."""
from __future__ import annotations

from datetime import datetime


def _num(v) -> float | None:
    """float(v) for numbers and numeric strings (the SDK order dict holds "1", "286.25");
    None for anything else."""
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _qty(v) -> str:
    f = _num(v)
    if f is None:
        return str(v) if v not in (None, "") else "?"
    return str(int(f)) if f == int(f) else f"{f:g}"


def _price(v) -> str:
    f = _num(v)
    return f" @ {f:,.2f}" if f is not None else ""


def compose(*, placed: list[dict], skipped: list[dict], errors: list[dict], now: datetime,
            enabled: bool, kill_active: bool) -> dict | None:
    """None when there is nothing to say (no placement, no error). Otherwise
    {title, text, priority, tags}: priority/tags are "high"/"warning" on any error, else
    "default"/"money_with_wings"."""
    if not placed and not errors:
        return None
    title = f"Autopilot {now.strftime('%H:%M')} - {len(placed)} placed"
    if errors:
        title += f", {len(errors)} error(s)"
    lines: list[str] = []
    for p in placed:
        qty = _qty(p.get("qty", p.get("shares")))
        px = _price(p.get("price", p.get("entry")))
        lines.append(f"- {p.get('side', '?')} {qty} {p.get('symbol', '?')}{px} "
                     f"({p.get('source', '?')})")
    if errors:
        lines.append("ERRORS")
        for e in errors:
            lines.append(f"- {e.get('stage', '?')}: {str(e.get('error', '?'))[:160]}")
    lines.append(f"skipped {len(skipped)} | enabled={enabled} kill={kill_active}")
    return {"title": title, "text": "\n".join(lines),
            "priority": "high" if errors else "default",
            "tags": "warning" if errors else "money_with_wings"}
