"""Standing-decisions ledger — the manager's open decisions and what would trigger them,
event-sourced as append-only JSONL under $ACTIVITY_DIR (default data/activity). Two row kinds:
"decision" (create/update/resolve — the LATEST row per id wins) and "check" (the nightly
trigger-watch result; latest per id = current watch state). Written by Claude sessions and the
evening check; read by `decisions_service` (the nightly evening check in the manager's note). The
autopilot never reads it -- its DECISIONS stage reads `webull_api/decisions_exec`
(executable_decisions.jsonl, queued by scripts/queue_decision.py). Mirrors manager_notes_store's pattern."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "decisions.jsonl"
_LOCK = threading.Lock()


def _dir() -> Path:
    return data_dir("activity", "ACTIVITY_DIR")


def load() -> list[dict]:
    f = _dir() / _FILE
    if not f.exists():
        return []
    out = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def fold(rows: list[dict]) -> list[dict]:
    """Current state: latest decision row per id (first-seen order), each with its latest
    check attached as `last_check` (None when never checked). Checks for unknown ids are
    ignored — a check can never invent a decision."""
    decisions: dict[str, dict] = {}
    checks: dict[str, dict] = {}
    for r in rows:
        rid = r.get("id")
        if not rid:
            continue
        if r.get("kind") == "decision":
            # dict assignment keeps the FIRST insertion position while taking the latest value —
            # exactly the "latest row wins, first-seen order" fold.
            decisions[rid] = {**r}
        elif r.get("kind") == "check":
            checks[rid] = r
    out = []
    for rid, d in decisions.items():
        d["last_check"] = checks.get(rid)
        out.append(d)
    return out


def append(rows: list[dict]) -> None:
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, default=str) + "\n")
