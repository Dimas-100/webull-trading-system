"""Ties the pure options paper engine to the JSON store + live option snapshots. The only
options-paper module that touches the network. Mirrors webull_web/paper_service.py."""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

from webull_api import market_data, options, options_chain
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.paper import options_engine as eng
from webull_api.paper.options_schema import OptionsPaperAccount
from webull_api.strategy.bars import to_ohlcv

from . import paper_options_store

try:
    ET = __import__("zoneinfo").ZoneInfo("America/New_York")
except Exception:                       # tzdata absent — approx ET (only affects DAY-expiry labels)
    ET = timezone(timedelta(hours=-5))

DEFAULT_CASH = 100000.0
HISTORY_CAP = 500
LOCK = threading.RLock()


def now_et() -> tuple[str, str]:
    now = datetime.now(ET)
    return now.isoformat(), now.strftime("%Y-%m-%d")


def load_account() -> OptionsPaperAccount:
    data = paper_options_store.load()
    if data is None:
        return eng.open_account(DEFAULT_CASH, now_et()[0])
    return OptionsPaperAccount.model_validate(data)


def save_account(acct: OptionsPaperAccount) -> None:
    eng.trim_history(acct, HISTORY_CAP)
    paper_options_store.save(acct.model_dump())


def account_occs(acct: OptionsPaperAccount) -> list[str]:
    occs = {lg.occ for u in acct.positions for lg in u.legs}
    occs |= {lg.occ for o in acct.open_orders for lg in o.legs}
    return sorted(occs)


def account_underlyings(acct: OptionsPaperAccount) -> set[str]:
    return {lg.underlying for u in acct.positions for lg in u.legs}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def marks_for(occs: list[str]) -> dict:
    """{occ: {bid, ask, last, mid}} from get_option_snapshot. Entitlement errors propagate
    (route -> 402); any other failure degrades to {} so orders rest / marks go null."""
    occs = [o for o in occs if o]
    if not occs:
        return {}
    try:
        raw = options.get_option_snapshot(",".join(occs))
    except MarketDataNotEntitledError:
        raise
    except Exception:
        return {}
    rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else []) or []
    out: dict[str, dict] = {}
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        bid = _f(row.get("bidPrice") if row.get("bidPrice") is not None else row.get("bid"))
        ask = _f(row.get("askPrice") if row.get("askPrice") is not None else row.get("ask"))
        last = _f(row.get("price") if row.get("price") is not None else row.get("last"))
        mid = (bid + ask) / 2 if (bid is not None and ask is not None) else last
        if sym:
            out[sym] = {"bid": bid, "ask": ask, "last": last, "mid": mid}
    return out


def spots_for(underlyings) -> dict[str, float]:
    out: dict[str, float] = {}
    for u in underlyings:
        try:
            # u may be an OCC root (leg.underlying); the quote endpoint wants the listing form.
            out[u] = market_data.spot_price(options_chain.fetch_symbol(u))
        except MarketDataNotEntitledError:
            raise
        except Exception:
            continue
    return out


def settle_closes_for(acct: OptionsPaperAccount, today_et: str) -> dict:
    """Best-effort {(underlying, exp_date): expiration-day close} for already-expired position
    units, from daily bars — so settlement prices intrinsic at the expiration-day close instead
    of whatever the spot happens to be when the user next refreshes. ANY failure (incl.
    not-entitled) degrades to missing keys; the engine then falls back to the current spot and
    stamps settle_basis="current_spot" on the settlement record."""
    needed: dict[str, set[str]] = {}
    for u in acct.positions:
        exp = min(lg.expiration for lg in u.legs)
        if exp < today_et:
            needed.setdefault(u.legs[0].underlying, set()).add(exp)
    out: dict = {}
    for under, exps in needed.items():
        try:
            bars = to_ohlcv(market_data.get_bars(options_chain.fetch_symbol(under), count="90"))
        except Exception:
            continue
        closes = {(b.get("time") or "")[:10]: b.get("close") for b in (bars or [])}
        for exp in exps:
            close = closes.get(exp)
            if close is not None:
                out[(under, exp)] = float(close)
    return out
