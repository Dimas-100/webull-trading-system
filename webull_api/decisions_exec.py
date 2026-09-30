"""Executable-decisions queue store. Sessions QUEUE decisions here; ONLY the armed autopilot
executor PLACES them (through the gate). This module is pure file I/O — it never imports trading,
safety, or anything that talks to the network, so writing a row can never place an order.

Fail-closed by construction: a malformed line is reported and skipped, never guessed at; `mark`
rewrites the whole file atomically (temp + os.replace) so a crash can't half-flip a status; a row
stops being executable the instant its status leaves "queued"."""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

QUEUE_PATH = Path(__file__).resolve().parent.parent / "data" / "activity" / "executable_decisions.jsonl"

_ASSETS = {"EQUITY", "OPTION"}
_SIDES = {"BUY", "SELL"}
_EQ_ORDER_TYPES = {"MARKET", "LIMIT"}
_RIGHTS = {"C", "P"}


def _path() -> Path:
    env = os.environ.get("WEBULL_DECISIONS_FILE", "").strip()
    return Path(env) if env else QUEUE_PATH


_LOCK_STALE_S = 30.0
_LOCK_WAIT_S = 5.0


@contextmanager
def _locked(p: Path):
    """Cross-process mutual exclusion for queue mutations. Without it, an append() landing between
    mark()'s read and its os.replace is silently dropped (review finding, Task 1). O_CREAT|O_EXCL
    lock file + retry; a lock older than _LOCK_STALE_S is a crashed writer and is broken. Waiting
    longer than _LOCK_WAIT_S raises — fail loudly, never guess about a queue that feeds an executor."""
    p.parent.mkdir(parents=True, exist_ok=True)
    lock = p.with_suffix(p.suffix + ".lock")
    deadline = time.monotonic() + _LOCK_WAIT_S
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > _LOCK_STALE_S:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() > deadline:
                raise TimeoutError(f"decisions queue lock held too long: {lock}")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            lock.unlink(missing_ok=True)
        except OSError:
            pass


def _numeric(v) -> bool:
    """Finite numbers only. NaN comparisons are always False, so a 'nan' price would sail
    through every `> cap` wall downstream (final-review finding C1) — reject it here."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _validate(row: dict) -> None:
    """Raise ValueError naming the offending field. Extra unknown fields are tolerated; any
    status is accepted here (append stamps 'queued'; later statuses are the executor's marks)."""
    if not isinstance(row, dict):
        raise ValueError("row must be a dict")
    if row.get("asset") not in _ASSETS:
        raise ValueError(f"asset must be one of {sorted(_ASSETS)}, got {row.get('asset')!r}")
    if not str(row.get("symbol") or "").strip():
        raise ValueError("symbol must be non-empty")
    if row.get("side") not in _SIDES:
        raise ValueError(f"side must be one of {sorted(_SIDES)}, got {row.get('side')!r}")
    trig = row.get("trigger")
    if not isinstance(trig, dict) or not str(trig.get("kind") or "").strip():
        raise ValueError("trigger must be a dict with a 'kind'")
    if not str(row.get("expires") or "").strip():
        raise ValueError("expires must be an ISO date string")
    if row["asset"] == "EQUITY":
        qty = row.get("qty")
        if qty != "ALL" and not _numeric(qty):
            raise ValueError(f"qty must be 'ALL' or numeric, got {qty!r}")
        if row.get("order_type") not in _EQ_ORDER_TYPES:
            raise ValueError(f"order_type must be one of {sorted(_EQ_ORDER_TYPES)}, got {row.get('order_type')!r}")
        if row["order_type"] == "LIMIT" and not _numeric(row.get("limit_price")):
            raise ValueError("limit_price required (numeric) for LIMIT")
    else:  # OPTION
        if row["side"] != "BUY":
            # v1 is long-only (no close flow), and the executor builds every option leg as a BUY.
            # A SELL-labeled option row would therefore place a risk-ADDING debit while skipping
            # the BUY-only cooling veto and open-orders guard (security review S1).
            raise ValueError("OPTION rows are long-only in v1: side must be BUY")
        opt = row.get("option")
        if not isinstance(opt, dict):
            raise ValueError("option dict required for OPTION rows")
        if not str(opt.get("expiry") or "").strip():
            raise ValueError("option.expiry must be an ISO date string")
        if not _numeric(opt.get("strike")):
            raise ValueError(f"option.strike must be numeric, got {opt.get('strike')!r}")
        if opt.get("right") not in _RIGHTS:
            raise ValueError(f"option.right must be one of {sorted(_RIGHTS)}, got {opt.get('right')!r}")
        q = opt.get("quantity")
        if not _numeric(q) or not float(q).is_integer() or float(q) < 1:
            raise ValueError(f"option.quantity must be a whole number >= 1, got {q!r}")
        if not _numeric(opt.get("limit_price")):
            raise ValueError(f"option.limit_price must be numeric, got {opt.get('limit_price')!r}")
        # Optional second leg -> a DEBIT VERTICAL. limit_price then means the NET debit ceiling.
        # The short strike must sit on the far side of the long strike for the structure to be a
        # debit (calls: short above; puts: short below) — the reverse would be a credit spread,
        # which this sleeve never sells.
        short = opt.get("short_strike")
        if short is not None:
            if not _numeric(short):
                raise ValueError(f"option.short_strike must be numeric, got {short!r}")
            long_k, short_k = float(opt["strike"]), float(short)
            if long_k == short_k:
                raise ValueError("vertical legs must have different strikes")
            if opt["right"] == "C" and short_k < long_k:
                raise ValueError("call vertical: short_strike must be ABOVE the long strike (debit only)")
            if opt["right"] == "P" and short_k > long_k:
                raise ValueError("put vertical: short_strike must be BELOW the long strike (debit only)")
            width = abs(short_k - long_k)
            debit = float(opt["limit_price"])
            if debit >= width:
                # max gain = width - debit; a debit at or above the width can never profit.
                raise ValueError(f"net debit {debit} must be less than the {width} strike width")


def append(row: dict) -> dict:
    """Validate the caller's row, stamp identity/queued status, persist one JSON line."""
    _validate(row)
    stored = dict(row)
    # Executor-owned fields may never be supplied by a writer: a caller-set first_seen would
    # pre-age the cooling veto (re-review residual on finding I2).
    for k in ("first_seen", "acted_ts", "detail"):
        stored.pop(k, None)
    stored["id"] = uuid.uuid4().hex
    stored["ts"] = datetime.now().isoformat()
    stored["status"] = "queued"
    p = _path()
    with _locked(p):
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(stored, ensure_ascii=False) + "\n")
    return stored


def load() -> tuple[list[dict], list[str]]:
    """(valid rows, error strings). Malformed lines are reported, never guessed; they stay on
    disk untouched so a human can inspect them."""
    p = _path()
    rows: list[dict] = []
    errors: list[str] = []
    seen_ids: set = set()
    if not p.exists():
        return rows, errors
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if not isinstance(row, dict) or not row.get("id") or not row.get("status"):
                raise ValueError("missing id/status")
            if row["id"] in seen_ids:
                # An id collision lets a hand-written row inherit another row's cooling stamp and
                # makes mark() flip both rows (re-review finding) — reject the later one outright.
                raise ValueError(f"duplicate id {row['id']}")
            _validate(row)
        except (ValueError, TypeError) as e:
            errors.append(f"line {n}: {e}")
            continue
        seen_ids.add(row["id"])
        rows.append(row)
    return rows, errors


def mark(row_id: str, status: str, detail: str | None = None) -> bool:
    """Flip one row's status (adding acted_ts, and detail when given). Atomic full-file rewrite;
    unparseable lines are carried through verbatim. False when the id isn't found."""
    p = _path()
    if not p.exists():
        return False
    with _locked(p):
        return _mark_locked(p, row_id, status, detail)


def stamp_first_seen(row_id: str, iso: str) -> bool:
    """Record the executor's FIRST observation of a row — the cooling basis a backdated ts can't
    forge (final-review finding I2). Sets first_seen only if absent; never touches status. Same
    lock + atomic rewrite as mark."""
    p = _path()
    if not p.exists():
        return False
    with _locked(p):
        out_lines: list[str] = []
        found = False
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                out_lines.append(line)
                continue
            if isinstance(row, dict) and row.get("id") == row_id and not row.get("first_seen"):
                row["first_seen"] = iso
                found = True
                out_lines.append(json.dumps(row, ensure_ascii=False))
            else:
                out_lines.append(line)
        if not found:
            return False
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return True


def _mark_locked(p: Path, row_id: str, status: str, detail: str | None) -> bool:
    out_lines: list[str] = []
    found = False
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            out_lines.append(line)
            continue
        if isinstance(row, dict) and row.get("id") == row_id:
            row["status"] = status
            row["acted_ts"] = datetime.now().isoformat()
            if detail is not None:
                row["detail"] = detail
            found = True
            out_lines.append(json.dumps(row, ensure_ascii=False))
        else:
            out_lines.append(line)
    if not found:
        return False
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return True
