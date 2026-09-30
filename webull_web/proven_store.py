"""Ownership overlay for the proven-strategy paper runner: which proven strategy (by trial_id) owns
each symbol on the isolated proven.json paper book. A small JSON map under $PAPER_DIR — so exits
target the right lots and two proven strategies can't fight over one symbol. Mirrors rsi2_store's
role (a code-maintained ownership overlay on a paper account). Tolerant of an absent/corrupt file."""
from __future__ import annotations

import json
from pathlib import Path

from webull_api.paths import data_dir
from . import store_io

_FILE = "proven_owners.json"


def _path() -> Path:
    return data_dir("paper", "PAPER_DIR") / _FILE


def load() -> dict:
    """symbol -> owning trial_id. Absent/corrupt -> {}."""
    f = _path()
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in d.items()} if isinstance(d, dict) else {}


def save(owners: dict) -> None:
    store_io.atomic_write_json(_path(), owners)
