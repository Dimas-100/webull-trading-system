"""Shared atomic file I/O for the JSON/JSONL stores.

A plain ``Path.write_text`` truncates the target *before* writing it, so a crash or
power-loss mid-write leaves a half-written (corrupt) file that the next ``load()``
cannot parse — losing the paper account, strategies, intents, or practice sessions.

``atomic_write_text`` writes to a temp file in the *same directory* and ``os.replace``s
it into place. ``os.replace`` is an atomic rename on the same filesystem on both Windows
and POSIX, so a reader always sees either the complete old content or the complete new
content — never a partial write. The temp file is fsync'd before the rename for durability,
and removed if anything fails (so no orphaned ``.tmp`` is left behind).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Atomically replace ``path``'s contents with ``text``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Same-directory temp so os.replace is an atomic rename (not a cross-device copy).
    # The "." prefix + ".tmp" suffix keep it out of any glob("*.json") store listing.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # Leave the original (if any) intact and don't orphan the temp file.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data: Any, *, indent: int | None = None) -> None:
    """Atomically write ``data`` as JSON to ``path``.

    json.dumps runs *before* the temp file is touched, so a non-serializable payload
    raises without disturbing the existing file.
    """
    text = json.dumps(data, indent=indent)
    atomic_write_text(path, text)
