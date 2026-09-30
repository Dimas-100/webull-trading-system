"""Tiingo supported-tickers list -> US stock/ETF universe rows (spec 2026-09-08 orb backtest §3.1).
Offline-only. The zip needs no token."""
from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from webull_api.tiingo import store

ZIP_URL = "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
FILENAME = "_tickers.csv"
META = "_tickers.json"
FIELDS = ("ticker", "exchange", "assetType", "priceCurrency", "startDate", "endDate")
US_EXCHANGES = frozenset({"NYSE", "NASDAQ", "NYSE ARCA", "NYSE MKT", "BATS"})
ASSET_TYPES = frozenset({"Stock", "ETF"})


def parse_zip(content: bytes) -> list[dict]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        text = z.read(name).decode("utf-8")
    return [{k: (r.get(k) or "").strip() for k in FIELDS} for r in csv.DictReader(io.StringIO(text))]


def fetch(session=None) -> list[dict]:
    resp = (session or requests.Session()).get(ZIP_URL, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"supported_tickers.zip: HTTP {resp.status_code}")
    return parse_zip(resp.content)


def us_equities(rows) -> list[dict]:
    return [r for r in rows
            if r.get("priceCurrency") == "USD" and r.get("assetType") in ASSET_TYPES
            and r.get("exchange") in US_EXCHANGES and r.get("startDate")]


def listed_between(rows, start: str, end: str) -> list[str]:
    """Tickers whose [startDate, endDate] overlaps [start, end]; a blank endDate means still listed."""
    out = set()
    for r in rows:
        s, e = r.get("startDate") or "", r.get("endDate") or ""
        if s and s <= end and (not e or e >= start):
            out.add(r["ticker"].upper())
    return sorted(out)


def path() -> Path:
    return store.dir_path() / FILENAME


def write(rows) -> Path:
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    os.replace(tmp, p)
    meta = {"rows": len(rows), "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (p.parent / META).write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return p


def read(p: Path | None = None) -> list[dict]:
    p = p or path()
    if not p.exists():
        return []
    with p.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def refresh(session=None) -> list[dict]:
    rows = us_equities(fetch(session))
    write(rows)
    return rows
