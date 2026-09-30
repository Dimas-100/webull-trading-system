"""Shared Finnhub key resolution for the web-layer data modules.

`fundamentals_data` delegates its `_key()` here (one copy of the load_dotenv + env-read logic).
`news.py` keeps its own inline key handling — its tests mock `news.load_dotenv` directly, so it
must retain that seam. The best-effort HTTP fetch itself stays per-module (each module's tests
mock `<module>.requests.get`), so only the key logic is centralized here — the consolidation
that does not disturb the existing test seams.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


def key() -> str:
    load_dotenv()
    return os.environ.get("FINNHUB_API_KEY", "").strip()


def has_key() -> bool:
    """True when a Finnhub key is configured (callers distinguish 'no data' from 'no key')."""
    return bool(key())
