"""File-backed options paper-account store: a single JSON file under $PAPER_DIR
(default ./data/paper/options.json, gitignored). Atomic writes via store_io."""
from __future__ import annotations

import json
from pathlib import Path

from webull_api.paths import data_dir
from . import store_io


def _path() -> Path:
    return data_dir("paper", "PAPER_DIR") / "options.json"


def load() -> dict | None:
    f = _path()
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        try:
            f.replace(f.with_suffix(f.suffix + ".corrupt"))
        except OSError:
            pass
        return None


def save(data: dict) -> dict:
    store_io.atomic_write_json(_path(), data)
    return data
