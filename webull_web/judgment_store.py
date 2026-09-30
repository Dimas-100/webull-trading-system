"""Append-only store for nightly entry-judgment verdicts (SHADOW layer — data, never enforcement).
One JSONL row per judged entry candidate per night, under $ACTIVITY_DIR (default data/activity),
gitignored. Written ONLY by judgment_service; read by the manager's note and future skill-reads.
Spec: docs/superpowers/specs/2026-08-11-entry-judgment-shadow-design.md"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_FILE = "judgment_verdicts.jsonl"
_LOCK = threading.Lock()


def _dir() -> Path:
    return data_dir("activity", "ACTIVITY_DIR")


def append(rows: list[dict]) -> int:
    with _LOCK:
        d = _dir()
        d.mkdir(parents=True, exist_ok=True)
        with (d / _FILE).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def load() -> list[dict]:
    f = _dir() / _FILE
    if not f.exists():
        return []
    out: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def today_rows(today: str) -> list[dict]:
    return [r for r in load() if r.get("date") == today]
