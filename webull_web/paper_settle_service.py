"""Settle-at-open step for the equity paper books (2026-07-27 execution-fidelity spec).

Fills queued (fill_policy='next_open') orders at the next session's official open, read from
daily bars, via the pure engine.settle_next_open. Idempotent and account-level: every evening
equity runner calls settle_book() FIRST (inside its lock, before deciding), so whichever runner
goes first does the real work and the rest find an empty queue. Fetches bars only for symbols
with queued orders; saves only on mutation. 100% paper — no `trading` import."""
from __future__ import annotations

import logging
from datetime import datetime

from webull_api import market_data
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.paper import engine as paper_engine
from webull_api.strategy.bars import to_ohlcv

from . import paper_service

_log = logging.getLogger(__name__)


def _bar_date(bar_time) -> str | None:
    """The bar's calendar date (YYYY-MM-DD). Local copy mirroring rsi2_service._bar_date —
    importing it from there would cycle (rsi2_service imports this module)."""
    s = str(bar_time)
    try:
        if s.isdigit():
            v = int(s)
            if v > 10_000_000_000:
                v //= 1000
            return datetime.utcfromtimestamp(v).date().isoformat()
        return datetime.fromisoformat(s).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def settle_book(*, load=None, save=None) -> dict:
    """Settle one paper book's queued orders. Returns {"settled", "rejected", "resting",
    "stale", "errors"} — stale = resting orders placed before today (surfaced by runner
    summaries so a never-filling queue is loud, not silent)."""
    load = load or paper_service.load_account
    save = save or paper_service.save_account
    iso, today = paper_service.now_et()
    acct = load()
    queued = [o for o in acct.open_orders if o.fill_policy == "next_open"]
    if not queued:
        return {"settled": [], "rejected": [], "resting": [], "stale": [], "errors": []}

    errors: list[str] = []
    bars_by: dict[str, list[dict]] = {}
    for sym in sorted({o.symbol for o in queued}):
        try:
            bars = to_ohlcv(market_data.get_bars(sym, "D", count="30"))
        except MarketDataNotEntitledError:
            raise
        except Exception:
            errors.append(f"{sym}: bar fetch failed")
            continue
        rows = []
        for b in bars:
            d = _bar_date(b["time"])
            if d:
                rows.append({"date": d, "open": b["open"]})
        bars_by[sym] = rows

    acct, fills, rejected, resting = paper_engine.settle_next_open(acct, bars_by, iso)
    if fills or rejected:
        save(acct)
    stale = [o for o in resting if o.placed_et_date < today]
    return {"settled": fills, "rejected": rejected, "resting": resting,
            "stale": stale, "errors": errors}
