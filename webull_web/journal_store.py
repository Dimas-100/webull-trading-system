"""JSONL append-logs for the journal, under the repo-anchored, gitignored data/journal (env
JOURNAL_DIR overrides). One file for fills, one for decisions, plus a small meta.json. Dedup by record id on append."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from webull_api.paths import data_dir
from webull_api.journal.schema import Fill, OptionTrade, PracticeDecision

from . import store_io

_FILLS = "fills.jsonl"
_DECISIONS = "decisions.jsonl"
_OPTION_TRADES = "option_trades.jsonl"
_META = "meta.json"

# A single in-process lock serializes the read-dedup-append critical section. Two overlapping
# journal syncs in the same process (e.g. an MCP tool call racing a runner's own append) could
# otherwise double-append. One process => an in-process Lock (not OS file locking) is the right
# tool. Appends are infrequent, so one lock for both logs is fine.
_LOCK = threading.Lock()


def _dir() -> Path:
    return data_dir("journal", "JOURNAL_DIR")


def _load_lines(name: str) -> list[dict]:
    f = _dir() / name
    if not f.exists():
        return []
    out: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # tolerate a partial/corrupt line (e.g. a crash mid-append)
    return out


def _dedup_by_id(rows: list[dict]) -> list[dict]:
    """Keep the first record per id, preserving order. Belt-and-suspenders against a
    concurrent double-append racing past the on-write dedup — makes reads idempotent
    regardless of duplicate lines, so win-rate/expectancy counts can't be skewed."""
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
        new = [r for r in records if r.get("id") not in existing]
        if new:
            d.mkdir(parents=True, exist_ok=True)
            with (d / name).open("a", encoding="utf-8") as fh:
                for r in new:
                    fh.write(json.dumps(r) + "\n")
        return len(new)


def append_fills(fills: list[Fill]) -> int:
    return _append(_FILLS, [f.model_dump() for f in fills])


def append_decisions(decisions: list[PracticeDecision]) -> int:
    return _append(_DECISIONS, [d.model_dump() for d in decisions])


def load_fills() -> list[Fill]:
    return [Fill.model_validate(r) for r in _dedup_by_id(_load_lines(_FILLS))]


def existing_fill_ids() -> set[str]:
    """Just the fill ids already on disk (no pydantic validation) — used by the ingest layer to
    skip the market-context fetch for already-journaled fills, making repeat syncs cheap."""
    return {r.get("id") for r in _load_lines(_FILLS) if r.get("id")}


def load_decisions() -> list[PracticeDecision]:
    return [PracticeDecision.model_validate(r) for r in _dedup_by_id(_load_lines(_DECISIONS))]


def append_option_trades(trades: list[OptionTrade]) -> int:
    return _append(_OPTION_TRADES, [t.model_dump() for t in trades])


def load_option_trades() -> list[OptionTrade]:
    return [OptionTrade.model_validate(r) for r in _dedup_by_id(_load_lines(_OPTION_TRADES))]


def existing_option_trade_ids() -> set[str]:
    return {r.get("id") for r in _load_lines(_OPTION_TRADES) if r.get("id")}


def write_meta(meta: dict) -> dict:
    store_io.atomic_write_json(_dir() / _META, meta)
    return meta


def read_meta() -> dict:
    f = _dir() / _META
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
