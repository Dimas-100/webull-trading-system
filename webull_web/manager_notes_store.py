"""Daily manager's-note store — append-only JSONL under $ACTIVITY_DIR (default data/activity).
One note per ET day, written by manager_note_service (the 9th suite step) — the durable record
of the full note (the ntfy push and the kept `lines` gather come from the same in-memory note
before it's stored). Mirrors netliq_store's pattern."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "manager_notes.jsonl"
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
    return sorted(out, key=lambda e: (str(e.get("date", "")), str(e.get("ts", ""))))


def append(entry: dict) -> None:
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
