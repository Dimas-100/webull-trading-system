"""Append-only JSONL run-log: one line per scheduled-runner execution, under $ACTIVITY_DIR
(default data/activity, gitignored). The Build-1 Monitor reads it to show a job's real last-run.
Writers: the code-backed scheduled runners (e.g. the RSI2 paper runner). Mirrors action_log."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "runs.jsonl"
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


def append(entries: list[dict]) -> int:
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, default=str) + "\n")
        return len(entries)


def last_for(key: str) -> dict | None:
    """The most recent run-log entry for a job key, or None."""
    match = [r for r in load() if r.get("key") == key]
    return match[-1] if match else None
