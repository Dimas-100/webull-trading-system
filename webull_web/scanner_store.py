"""Append-only JSONL stores for the scanner efficacy ledger (data/scanner, spec 2026-07-21):
snapshots.jsonl (one record per ranked pick per day, id "<date>:<symbol>") and scores.jsonl
(one record per matured window, id "<date>:<symbol>:<window>"). Mirrors journal_store's shape
(dedup by id on append AND read, corrupt lines skipped). Writers: scan_ledger_service only."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir

_SNAPSHOTS = "snapshots.jsonl"
_SCORES = "scores.jsonl"
_LOCK = threading.Lock()


def _dir() -> Path:
    return data_dir("scanner", "WEBULL_SCANNER_DIR")


def _load_lines(name: str) -> list[dict]:
    f = _dir() / name
    if not f.exists():
        return []
    out: list[dict] = []
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


def _dedup_by_id(rows: list[dict]) -> list[dict]:
    seen: set = set()
    out: list[dict] = []
    for r in rows:
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def _append(name: str, records: list[dict]) -> int:
    with _LOCK:
        d = _dir()
        existing = {r.get("id") for r in _load_lines(name)}
        new = _dedup_by_id([r for r in records if r.get("id") not in existing])
        if new:
            d.mkdir(parents=True, exist_ok=True)
            with (d / name).open("a", encoding="utf-8") as fh:
                for r in new:
                    fh.write(json.dumps(r, default=str) + "\n")
        return len(new)


def append_snapshots(records: list[dict]) -> int:
    return _append(_SNAPSHOTS, records)


def load_snapshots() -> list[dict]:
    return _dedup_by_id(_load_lines(_SNAPSHOTS))


def append_scores(records: list[dict]) -> int:
    return _append(_SCORES, records)


def load_scores() -> list[dict]:
    return _dedup_by_id(_load_lines(_SCORES))
