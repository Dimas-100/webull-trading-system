"""CSV-per-symbol store for Tiingo daily history (spec 2026-09-07 tiingo depth §3.2). Adjusted
AND raw columns are kept: bars use adj_*, the raw columns stay for audit."""
from __future__ import annotations

import csv
import json
import math
import os
from pathlib import Path

from webull_api import paths

COLUMNS = ("date", "open", "high", "low", "close", "volume",
           "adj_open", "adj_high", "adj_low", "adj_close", "adj_volume", "div_cash", "split_factor")
_API_KEYS = {"open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume",
             "adj_open": "adjOpen", "adj_high": "adjHigh", "adj_low": "adjLow", "adj_close": "adjClose",
             "adj_volume": "adjVolume", "div_cash": "divCash", "split_factor": "splitFactor"}
_MANIFEST = "_manifest.json"
_PRICE_COLS = {"open", "high", "low", "close", "adj_open", "adj_high", "adj_low", "adj_close"}


def dir_path() -> Path:
    return paths.data_dir("tiingo", "TIINGO_DIR")


def path(symbol: str) -> Path:
    return dir_path() / f"{symbol.strip().upper().replace(' ', '_')}.csv"


def symbols() -> list[str]:
    """Every symbol with a CSV directly in the store, sorted. Skips leading-underscore files
    (``_manifest.json``, ``_tickers.csv``) and any subdirectory (e.g. ``universe/``)."""
    d = dir_path()
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.csv") if not p.stem.startswith("_"))


def _f(v, col: str | None = None) -> float | None:
    try:
        f = float(v)
        if col in _PRICE_COLS and not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        if col in _PRICE_COLS:
            return None
        return 0.0


def from_api(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows or []:
        d = str(r.get("date", ""))[:10]
        if len(d) != 10:
            continue
        row = {"date": d}
        skip = False
        for col, key in _API_KEYS.items():
            val = r.get(key, 1.0 if col == "split_factor" else 0.0)
            parsed = _f(val, col)
            if parsed is None:
                skip = True
                break
            row[col] = parsed
        if not skip:
            out.append(row)
    return out


def write(symbol: str, rows: list[dict]) -> int:
    by_date = {}
    for r in rows:
        by_date[r["date"]] = r
    ordered = [by_date[d] for d in sorted(by_date)]
    p = path(symbol)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(COLUMNS), extrasaction="ignore")
        w.writeheader()
        for r in ordered:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    os.replace(tmp, p)
    return len(ordered)


def read(symbol: str) -> list[dict]:
    p = path(symbol)
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            row = {"date": r.get("date", "")}
            if len(row["date"]) != 10:
                continue
            skip = False
            for c in COLUMNS[1:]:
                parsed = _f(r.get(c), c)
                if parsed is None:
                    skip = True
                    break
                row[c] = parsed
            if not skip:
                out.append(row)
    out.sort(key=lambda r: r["date"])
    return out


def last_date(symbol: str) -> str | None:
    rows = read(symbol)
    return rows[-1]["date"] if rows else None


def needs_full_refetch(new_rows: list[dict]) -> bool:
    """A split or dividend in the new rows shifts every adjusted value before it."""
    return any(r.get("split_factor", 1.0) != 1.0 or r.get("div_cash", 0.0) != 0.0 for r in new_rows)


def read_manifest() -> dict:
    p = dir_path() / _MANIFEST
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {}


def write_manifest(entries: dict) -> None:
    p = dir_path() / _MANIFEST
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entries, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
