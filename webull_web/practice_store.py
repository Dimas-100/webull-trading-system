"""File-backed practice-session store: JSON under $PRACTICE_DIR (default ./data/practice, gitignored)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from webull_api.paths import data_dir

from . import store_io


def _dir() -> Path:
    return data_dir("practice", "PRACTICE_DIR")


def _safe(sid: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", sid or "") or "session"


def save_session(sid: str, data: dict) -> dict:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    sid = _safe(sid)
    out = {**data, "id": sid}
    store_io.atomic_write_json(d / f"{sid}.json", out)
    return out


def list_sessions() -> list[dict]:
    d = _dir()
    if not d.exists():
        return []
    items = []
    for f in sorted(d.glob("*.json")):
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
            items.append({"id": s.get("id"), "symbol": s.get("symbol"), "status": s.get("status")})
        except Exception:
            continue
    return items


def get_session(sid: str) -> dict:
    f = _dir() / f"{_safe(sid)}.json"
    if not f.exists():
        raise FileNotFoundError(sid)
    return json.loads(f.read_text(encoding="utf-8"))


def delete_session(sid: str) -> dict:
    f = _dir() / f"{_safe(sid)}.json"
    if f.exists():
        f.unlink()
    return {"deleted": _safe(sid)}
