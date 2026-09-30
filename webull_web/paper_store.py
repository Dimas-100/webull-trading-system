"""File-backed paper-account store: a single JSON file under $PAPER_DIR
(default ./data/paper, gitignored)."""
from __future__ import annotations

import json
from pathlib import Path

from webull_api.paths import data_dir
from . import store_io


def _path(name: str = "default") -> Path:
    return data_dir("paper", "PAPER_DIR") / f"{name}.json"


def load(name: str = "default") -> dict | None:
    """Load a named paper account (default 'default'; the proven-strategy sleeve uses 'proven')."""
    f = _path(name)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # A corrupt file (external edit / disk issue) must not 500 the account forever.
        # Quarantine it (so the data isn't silently lost) and start fresh — load_account()
        # treats None as "open a new default account", matching the no-file path.
        try:
            f.replace(f.with_suffix(f.suffix + ".corrupt"))
        except OSError:
            pass
        return None


def save(data: dict, name: str = "default") -> dict:
    store_io.atomic_write_json(_path(name), data)
    return data
