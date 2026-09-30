"""Owner contributions ledger — append-only JSONL under $ACTIVITY_DIR (default data/activity,
gitignored). Records money the owner adds to (positive) or withdraws from (negative) the
managed real account. Read by `manager_note_service` and by `flows_service`, which is also the
only writer left — it `append()`s a row when it detects a new deposit. The POST route went with
the old owner pages (2026-09-10). Mirrors webull_api/run_log.py's store pattern."""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "contributions.jsonl"
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
    return sorted(out, key=lambda e: (str(e.get("date", "")), str(e.get("created_at", ""))))


def append(entry: dict) -> dict:
    rec = {"id": uuid.uuid4().hex[:12], **entry}
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
    return rec
