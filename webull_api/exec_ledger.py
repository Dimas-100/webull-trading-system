"""Append-only JSONL decision-mark ledger for SUBMITTED real equity orders (execution-quality
slice, spec 2026-07-21). Written ONLY by trading.place's post-submit fail-open capture; readers
derive slippage fresh (webull_api/exec_quality.py). Mirrors action_log's shape (dedup by id,
corrupt lines skipped). NEVER imports trading or safety."""
from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from webull_api.paths import data_dir

_FILE = "decisions.jsonl"
_LOCK = threading.Lock()
_ET = ZoneInfo("America/New_York")


def _dir() -> Path:
    return data_dir("exec", "WEBULL_EXEC_DIR")


def _load_lines() -> list[dict]:
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


def load() -> list[dict]:
    """All decision records, deduped by id (first occurrence wins), order preserved."""
    seen: set = set()
    out: list[dict] = []
    for r in _load_lines():
        rid = r.get("id")
        if rid in seen:
            continue
        seen.add(rid)
        out.append(r)
    return out


def append(entries: list[dict]) -> int:
    """Append entries whose id is not already on disk. Returns the count actually written."""
    with _LOCK:
        d = _dir()
        existing = {r.get("id") for r in _load_lines()}
        new = [e for e in entries if e.get("id") not in existing]
        if new:
            d.mkdir(parents=True, exist_ok=True)
            with (d / _FILE).open("a", encoding="utf-8") as fh:
                for e in new:
                    fh.write(json.dumps(e, default=str) + "\n")
        return len(new)


_MARK_TIMEOUT_S = 2.0


def _mark(symbol: str) -> float | None:
    """Best-effort decision mark, BOUNDED: the snapshot fetch runs on a daemon thread with a
    short join so a hung quotes endpoint can never delay place()'s return after a live submit.
    A failure OR timeout yields None; the ledger append still happens either way."""
    out: list[float] = []

    def _fetch() -> None:
        try:
            from webull_api import market_data
            out.append(float(market_data.spot_price(symbol)))
        except Exception:
            pass

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()
    t.join(_MARK_TIMEOUT_S)
    return out[0] if out else None


def record_submit(account_id: str, order: dict, *, env: str) -> int:
    """Build + append ONE decision record for a just-submitted real equity order."""
    oid = order.get("client_order_id")
    if not oid:
        return 0
    symbol = str(order.get("symbol", "")).upper()
    rec = {
        "id": str(oid),
        "ts": datetime.now(_ET).isoformat(),
        "account_id": account_id,
        "symbol": symbol,
        "side": str(order.get("side", "")).upper(),
        "order_type": str(order.get("order_type", "")).upper(),
        "quantity": order.get("quantity"),
        "limit_price": order.get("limit_price"),
        "stop_price": order.get("stop_price"),
        "mark": _mark(symbol) if symbol else None,
        "env": env,
    }
    return append([rec])
