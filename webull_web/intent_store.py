"""File-backed order-intent store: pending Desktop->web order drafts as JSON under
$ORDER_INTENTS_DIR (default ./data/intents, gitignored). Pure I/O — callers stamp timestamps."""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from webull_api.paths import data_dir

from . import store_io


def _dir() -> Path:
    return data_dir("intents", "ORDER_INTENTS_DIR")


def _safe(iid: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", iid or "") or "intent"


def save_intent(data: dict) -> dict:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    iid = _safe(data.get("id") or uuid.uuid4().hex)
    out = {**data, "id": iid}
    store_io.atomic_write_json(d / f"{iid}.json", out, indent=2)
    return out


def _load_all() -> list[dict]:
    d = _dir()
    if not d.exists():
        return []
    items = []
    for f in sorted(d.glob("*.json")):
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
    return items


def list_intents(now_iso: str) -> list[dict]:
    return [i for i in _load_all()
            if i.get("status") == "pending" and (i.get("expires_at") or "") > now_iso]


def get_intent(iid: str) -> dict:
    f = _dir() / f"{_safe(iid)}.json"
    if not f.exists():
        raise FileNotFoundError(iid)
    return json.loads(f.read_text(encoding="utf-8"))


def set_status(iid: str, status: str) -> dict:
    data = get_intent(iid)
    data["status"] = status
    store_io.atomic_write_json(_dir() / f"{_safe(iid)}.json", data, indent=2)
    return data


def annotate(symbol: str, side: str, now_iso: str, **fields) -> dict | None:
    """Merge `fields` into the most-recent PENDING, unexpired intent matching `symbol`
    (case-insensitive) + `side`; persist (same id) and return it. None if no match.
    Pure I/O — the caller stamps any timestamps in `fields`."""
    sym = (symbol or "").strip().upper()
    sd = (side or "").strip().upper()
    cands = [i for i in list_intents(now_iso)
             if (i.get("symbol") or "").strip().upper() == sym
             and (i.get("side") or "").strip().upper() == sd]
    if not cands:
        return None
    target = max(cands, key=lambda i: i.get("created_at") or "")
    return save_intent({**target, **fields})
