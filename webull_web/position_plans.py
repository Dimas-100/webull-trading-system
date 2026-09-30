"""File-backed plan-of-record store: per-symbol structural stop/target recorded when an entry is
drafted, so the protective-stop reconcile (webull_api/reconcile.py) uses the real structural stop
rather than only a percentage backstop. Pure I/O — callers stamp timestamps. Mirrors intent_store."""
from __future__ import annotations

import json
import re
from pathlib import Path

from webull_api.paths import data_dir

from . import store_io


def _dir() -> Path:
    return data_dir("plans", "POSITION_PLANS_DIR")


def _safe(symbol: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "", (symbol or "").strip().upper()) or "SYM"


def save_plan(data: dict) -> dict:
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    sym = _safe(data.get("symbol"))
    out = {**data, "symbol": sym}
    store_io.atomic_write_json(d / f"{sym}.json", out, indent=2)
    return out


def get_plan(symbol: str) -> dict | None:
    f = _dir() / f"{_safe(symbol)}.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None


def all_plans() -> dict[str, dict]:
    d = _dir()
    if not d.exists():
        return {}
    out: dict[str, dict] = {}
    for f in sorted(d.glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(p, dict) and p.get("symbol"):
            out[p["symbol"]] = p
    return out
