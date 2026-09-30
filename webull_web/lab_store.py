"""File-backed lab store under $LAB_DIR (default ./data/lab, gitignored). Mirrors
strategy_store/paper_store: atomic writes via store_io; a corrupt index/meta is quarantined, not
fatal. Split persistence — a compact library index (NO bar arrays) + heavy per-trial files +
proven records + an append-only events log + meta.json holding the monotone lifetime M counter."""
from __future__ import annotations

import json
import re
from pathlib import Path

from webull_api.lab.schema import (CycleReport, PaperProvenRecord, ProvingState,
                                   StrategyRecord, TrialBook)
from webull_api.paths import data_dir

from . import store_io

_META_DEFAULT = {"last_advance_date": "", "M": 0,
                 "last_cycle_date": "", "cycle_seq": 0, "last_cycle": None, "stagnation": {}}


def lab_dir() -> Path:
    return data_dir("lab", "LAB_DIR")


def _safe(rec_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "", rec_id or "") or "rec"


def _read_json_quarantine(path: Path):
    """Parse JSON; on a corrupt file quarantine it (.corrupt) and return None (start-fresh)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        try:
            path.replace(path.with_suffix(path.suffix + ".corrupt"))
        except OSError:
            pass
        return None


def load_library() -> list[StrategyRecord]:
    f = lab_dir() / "library.json"
    if not f.exists():
        return []
    data = _read_json_quarantine(f)
    if not isinstance(data, list):
        return []
    out: list[StrategyRecord] = []
    for r in data:
        try:
            out.append(StrategyRecord.model_validate(r))
        except Exception:
            continue
    return out


def save_library(records: list[StrategyRecord]) -> None:
    store_io.atomic_write_json(lab_dir() / "library.json",
                              [r.model_dump() for r in records], indent=2)


def get_record(rec_id: str) -> StrategyRecord:
    for r in load_library():
        if r.id == rec_id:
            return r
    raise FileNotFoundError(rec_id)


def upsert_record(rec: StrategyRecord) -> None:
    out = [r for r in load_library() if r.id != rec.id]
    out.append(rec)
    save_library(out)


def load_trial(trial_id: str) -> ProvingState:
    f = lab_dir() / "trials" / f"{_safe(trial_id)}.json"
    if not f.exists():
        raise FileNotFoundError(trial_id)
    return ProvingState.model_validate(json.loads(f.read_text(encoding="utf-8")))


def save_trial(state: ProvingState) -> None:
    store_io.atomic_write_json(lab_dir() / "trials" / f"{_safe(state.trial_id)}.json",
                              state.model_dump())


def load_all_books() -> dict[str, TrialBook]:
    d = lab_dir() / "trials"
    if not d.exists():
        return {}
    out: dict[str, TrialBook] = {}
    for f in sorted(d.glob("*.json")):
        try:
            state = ProvingState.model_validate(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue
        if state.book is not None:
            out[state.trial_id] = state.book
    return out


def load_proven() -> list[PaperProvenRecord]:
    d = lab_dir() / "proven"
    if not d.exists():
        return []
    out: list[PaperProvenRecord] = []
    for f in sorted(d.glob("*.json")):
        try:
            out.append(PaperProvenRecord.model_validate(json.loads(f.read_text(encoding="utf-8"))))
        except Exception:
            continue
    return out


def save_proven(rec: PaperProvenRecord) -> None:
    rid = _safe(str(rec.gate_b.get("trial_id") or rec.strategy.name))
    store_io.atomic_write_json(lab_dir() / "proven" / f"{rid}.json", rec.model_dump())


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _reconstruct_meta_from_cycles() -> dict:
    """Rebuild the load-bearing meta fields from the append-only cycles.jsonl after a corrupt
    meta.json — so a corruption does NOT silently reset M to 0 (which would discard the accumulated
    multiple-testing correction, letting hypotheses be re-tested penalty-free, AND defeat the
    same-day guard). M = the max m_after ever recorded; dates/seq/stagnation from the latest cycle.
    A genuinely empty/absent cycles.jsonl is a real cold start (M=0). Robust to torn/malformed lines
    (int fields coerced via _as_int, so the recovery path itself never crashes). CAVEAT: if a crash
    landed between the meta write and the cycles append AND meta.json was later corrupted, M is
    rebuilt at most one Gate-A batch low — bounded, and far safer than the pre-change M=0 reset."""
    cycles = _read_all_cycles()
    if not cycles:
        return dict(_META_DEFAULT)
    last = cycles[-1]
    m = max((_as_int(c.get("m_after")) for c in cycles), default=0)
    return {**_META_DEFAULT, "M": m,
            "last_cycle_date": last.get("cycle_date", "") or "",
            "last_advance_date": last.get("cycle_date", "") or "",
            "cycle_seq": _as_int(last.get("cycle_seq")),
            "last_cycle": last,
            "stagnation": last.get("stagnation", {}) or {}}


def read_meta() -> dict:
    f = lab_dir() / "meta.json"
    if not f.exists():
        return dict(_META_DEFAULT)
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(m, dict):
            raise ValueError("meta.json is not a JSON object")
    except (json.JSONDecodeError, OSError, ValueError):
        # A corrupt meta must NOT silently reset M=0 — that would discard the accumulated
        # multiple-testing correction (letting hypotheses be re-tested penalty-free) and defeat the
        # same-day guard. Quarantine the bad file and reconstruct M + dates from cycles.jsonl.
        try:
            f.replace(f.with_suffix(f.suffix + ".corrupt"))
        except OSError:
            pass
        return _reconstruct_meta_from_cycles()
    return {**_META_DEFAULT, **m}


def write_meta(meta: dict) -> None:
    store_io.atomic_write_json(lab_dir() / "meta.json", meta)


def bump_m(n: int = 1) -> int:
    """Monotone increment of the lifetime multiple-testing counter M; returns the new value."""
    meta = read_meta()
    meta["M"] = int(meta.get("M", 0)) + n
    write_meta(meta)
    return meta["M"]


def append_event(event: dict) -> None:
    d = lab_dir()
    d.mkdir(parents=True, exist_ok=True)
    with (d / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event) + "\n")


def read_tombstones(limit: int = 50) -> list[dict]:
    f = lab_dir() / "events.jsonl"
    if not f.exists():
        return []
    out: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "gate_a_failed":
            out.append(ev)
    return out[-limit:]


def append_cycle(report: CycleReport) -> None:
    d = lab_dir()
    d.mkdir(parents=True, exist_ok=True)
    with (d / "cycles.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(report.model_dump()) + "\n")


def _read_all_cycles() -> list[dict]:
    f = lab_dir() / "cycles.jsonl"
    if not f.exists():
        return []
    out: list[dict] = []
    for line in f.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def read_cycles(limit: int = 20) -> list[dict]:
    return _read_all_cycles()[-limit:]
