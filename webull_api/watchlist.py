"""Read-only watchlist mirror: the user's Webull lists + their symbols.

Mirrors market_data.py's entitlement handling. Deliberately wraps ONLY the read
methods of data_client().watchlist — no create/add/remove/update is exposed, so
the app can never modify the user's Webull lists.
"""
from __future__ import annotations

from webull.core.exception.exceptions import ServerException

from .client import data_client
from .market_data import MarketDataNotEntitledError, _is_entitlement_error


def _check(res):
    if res.status_code != 200:
        raise RuntimeError(f"watchlist error {res.status_code}: {res.text}")
    return res.json()


def _call(fn, *args):
    try:
        return _check(fn(*args))
    except ServerException as exc:
        if _is_entitlement_error(exc):
            raise MarketDataNotEntitledError("Market data not subscribed.") from exc
        raise


def _rows(payload, *keys):
    """Normalize a list-or-enveloped response to a list of dict rows."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in keys:
            v = payload.get(k)
            if isinstance(v, list):
                return v
    return []


def get_watchlists() -> list[dict]:
    """All of the user's Webull watchlists as {id, name, sort}."""
    raw = _call(data_client().watchlist.get_watchlist)
    out = []
    for w in _rows(raw, "data", "watchlists"):
        wid = w.get("watchlist_id") or w.get("id")
        if wid:
            out.append({"id": wid, "name": w.get("name") or "", "sort": w.get("sort")})
    return out


def get_watchlist_symbols(watchlist_id: str) -> list[dict]:
    """Instruments in one watchlist as {symbol, name, exchange}."""
    raw = _call(data_client().watchlist.get_instruments, watchlist_id)
    out = []
    for it in _rows(raw, "instruments", "data", "items"):
        sym = it.get("symbol") or it.get("ticker")
        if sym:
            out.append({"symbol": sym, "name": it.get("name"), "exchange": it.get("exchange_code") or it.get("exchange")})
    return out
