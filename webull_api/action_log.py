"""Append-only JSONL action-log (the 'why' behind managed actions), under $ACTIVITY_DIR
(default data/activity, gitignored). Dedup by record id. Mirrors webull_web/journal_store's shape.
Writers: the seed script, the log_action MCP tool, and (Build 2) the scheduled runners. Readers
(via load()): the manager's note, the entry judgment, the scorecard and north-star proof bar, the
options entry service and the RSI2-real attribution."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "actions.jsonl"
_LOCK = threading.Lock()


def _dir() -> Path:
    return data_dir("activity", "ACTIVITY_DIR")


def _load_lines() -> list[dict]:
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


def load() -> list[dict]:
    """All entries, deduped by id (first occurrence wins), order preserved."""
    seen: set = set()
    out: list[dict] = []
    for r in _load_lines():
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def append(entries: list[dict]) -> int:
    """Append entries whose id is not already on disk. Returns the count actually written."""
    with _LOCK:
        d = _dir()
        existing = {r.get("id") for r in _load_lines()}
        new = [e for e in entries if e.get("id") not in existing]
        if new:
            d.mkdir(parents=True, exist_ok=True)
            with (d / _FILE).open("a", encoding="utf-8") as fh:
                for e in new:
                    fh.write(json.dumps(e, default=str) + "\n")
        return len(new)
