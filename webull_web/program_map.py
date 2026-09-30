"""Render the program map's tables from the track registry + the routine (spec 2026-09-07 program
map §3.2). Pure text in, text out; scripts/render_program_map.py does the file I/O."""
from __future__ import annotations

import re

from .routine import ROUTINE
from .tracks import MONEY_WORD, TRACKS, label_for

SECTIONS = ("grid", "cells", "where", "routine")
_KEYS_IN_ORDER = ("swing_rsi2", "swing_pullback")
_FIELDS = (("Plan doc", "plan_doc"), ("Book", "book"), ("Book source of truth", "book_source"),
           ("Universe", "universe"), ("Entry", "entry"), ("Exit", "exit"), ("Schedule", "schedule"),
           ("Data", "data"), ("Journal", "journal"), ("Kill switch", "kill_switch"), ("Review", "review"))


def _esc(v) -> str:
    if isinstance(v, (tuple, list)):
        v = " · ".join(f"`{x}`" for x in v)
    return str(v).replace("|", "\\|")


def grid_table(tracks=TRACKS) -> str:
    by = {(t.key, t.money): t for t in tracks}
    keys = list(dict.fromkeys(t.key for t in tracks))
    lines = ["| Track | Paper | Real |", "|---|---|---|"]
    for k in keys:
        paper = by.get((k, "paper")) or by.get((k, "none"))
        real = by.get((k, "real"))
        p = _esc(f"{paper.state.upper()} · {paper.summary}") if paper else "—"
        r = _esc(f"{real.state.upper()} · {real.summary}") if real else "—"
        lines.append(f"| {_esc(label_for(k))} | {p} | {r} |")
    return "\n".join(lines)


def cells_section(tracks=TRACKS) -> str:
    out = []
    for t in tracks:
        out.append(f"### {t.label} · {MONEY_WORD[t.money]}\n")
        out.append("| Field | Value |")
        out.append("|---|---|")
        for title, attr in _FIELDS:
            out.append(f"| {title} | {_esc(getattr(t, attr))} |")
        out.append("")
    return "\n".join(out).rstrip()


def where_table(tracks=TRACKS) -> str:
    lines = ["| Track | Money | Data | Journal | Kill switch |", "|---|---|---|---|---|"]
    for t in tracks:
        lines.append(f"| {_esc(t.label)} | {MONEY_WORD[t.money]} | {_esc(t.data)} | {_esc(t.journal)} | {_esc(t.kill_switch)} |")
    return "\n".join(lines)


def routine_table(rows=ROUTINE) -> str:
    lines = ["| Time (ET) | Track | What | Who | Evidence | Late after |", "|---|---|---|---|---|---|"]
    for r in rows:
        when = r.time or {"friday": "Friday", "monthly": "last Friday"}.get(r.days, "—")
        who = {"task": "scheduled task", "owner": "you", "external": "external", "market": "market"}[r.kind]
        if r.paused:
            who = f"PAUSED — {_esc(r.paused)}"
        ev = f"`{r.evidence}`" if r.evidence else "—"
        late = f"{r.grace_min} min" if r.kind in ("task", "owner") and r.time else "—"
        lines.append(f"| {when} | {_esc(label_for(r.track))} | {_esc(r.label)} | {who} | {ev} | {late} |")
    return "\n".join(lines)


_RENDERERS = {"grid": grid_table, "cells": cells_section, "where": where_table, "routine": routine_table}


def render(text: str) -> str:
    # The \n? on both sides of the body is deliberate: a freshly-created region has its start
    # and end markers on adjacent lines with nothing between them (no blank line), and that
    # empty region must still render — not just a region that already holds stale content.
    def sub(m: re.Match) -> str:
        name = m.group(1)
        if name not in _RENDERERS:
            return m.group(0)
        return f"<!-- map:{name}:start -->\n{_RENDERERS[name]()}\n<!-- map:{name}:end -->"
    return re.sub(r"<!-- map:(\w+):start -->\n?.*?\n?<!-- map:\1:end -->", sub, text, flags=re.S)
