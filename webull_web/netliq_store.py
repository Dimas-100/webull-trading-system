"""Daily net-liq history — append-only JSONL under $ACTIVITY_DIR (default data/activity). One line per ET day:
the real book + both paper books marked at that evening's prices. Written by
netliq_snapshot_service (the 7th suite step); read by the kestrel feed, flows_service and
manager_note_service. A book that can't be valued is null — never fabricated. Mirrors
webull_api/run_log.py's store pattern."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "netliq_history.jsonl"
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
    return sorted(out, key=lambda e: str(e.get("date", "")))


def append(entry: dict) -> None:
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
