"""Shared paper-account helpers used by the (removed 2026-09-29) paper-trading MCP and the paper
runners/services in this package. Ties the pure paper engine + paper store + live snapshot
prices together. Import-light by design, so any caller can import it freely.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from webull_api import market_data
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.paper import engine as paper_engine
from webull_api.paper.schema import PaperAccount

from . import paper_store

try:
    ET = __import__("zoneinfo").ZoneInfo("America/New_York")
except Exception:  # tzdata not installed (e.g. bare Windows) — approx ET, only affects DAY-expiry labels
    ET = timezone(timedelta(hours=-5))

DEFAULT_CASH = 100000.0
HISTORY_CAP = 500  # bound the persisted order history so the JSON file stays small

# Serializes a whole load->mutate->save transaction on the single paper account file.
# The MCP server can field overlapping tool calls, so without this two mutations could read
# the same account and the second save would clobber the first (a lost order/fill). One
# process => an in-process lock.
LOCK = threading.RLock()


def now_et() -> tuple[str, str]:
    now = datetime.now(ET)
    return now.isoformat(), now.strftime("%Y-%m-%d")


def last_prices(symbols) -> dict[str, float]:
    syms = sorted({s.upper() for s in symbols if s})
    if not syms:
        return {}
    try:
        raw = market_data.get_snapshot(",".join(syms))
    except MarketDataNotEntitledError:
        raise  # surface the missing-entitlement signal (mapped to HTTP 402)
    except Exception:
        return {}  # transient upstream failure: degrade gracefully — orders just don't advance, marks null
    rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else []) or []
    out: dict[str, float] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        px = row.get("price") or row.get("close") or row.get("last")
        if sym and px is not None:
            try:
                out[sym] = float(px)
            except (TypeError, ValueError):
                pass
    return out


def load_account() -> PaperAccount:
    data = paper_store.load()
    if data is None:
        return paper_engine.open_account(DEFAULT_CASH, now_et()[0])
    return PaperAccount.model_validate(data)


def account_symbols(acct: PaperAccount) -> set[str]:
    return set(acct.positions.keys()) | {o.symbol for o in acct.open_orders}


def save_account(acct: PaperAccount) -> None:
    paper_engine.trim_history(acct, HISTORY_CAP)
    paper_store.save(acct.model_dump())
