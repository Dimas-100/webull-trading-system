"""Read-only HTTP market data: quotes, snapshots, historical OHLCV bars."""
from __future__ import annotations

from webull.core.exception.exceptions import ServerException
from webull.data.common.category import Category
from webull.data.common.timespan import Timespan

from .client import data_client

# Shown to the user when Webull rejects a quotes request for lack of an entitlement.
# The fix is free: claim "Nasdaq Basic" under the OpenAPI Advanced Quotes Center.
ENTITLEMENT_MESSAGE = (
    "Market data not subscribed. Claim the free Nasdaq Basic tier under "
    "Webull OpenAPI → Advanced Quotes (Advanced Quotes Center)."
)


class MarketDataNotEntitledError(RuntimeError):
    """Webull rejected a market-data request for lack of a quotes entitlement.

    Distinct from a broken signature/token: the request authenticated fine, but the
    account/app has no market-data subscription. Webull returns HTTP 401 with
    error_code 'Unauthorized' and message 'Insufficient permission, please subscribe
    to stock quotes.' — a *free* Nasdaq Basic claim clears it (no Level 2 needed).
    """


def _is_entitlement_error(exc: ServerException) -> bool:
    """True for the 'please subscribe to stock quotes' 401, not other 401s/errors."""
    msg = (getattr(exc, "error_msg", "") or "").lower()
    return getattr(exc, "http_status", None) == 401 and (
        "subscribe" in msg or "insufficient permission" in msg
    )


def _check(res):
    if res.status_code != 200:
        raise RuntimeError(f"market data error {res.status_code}: {res.text}")
    return res.json()


def _call(fn, *args, **kwargs):
    """Invoke an SDK market-data call, translating the entitlement 401 into a
    typed error and letting every other failure propagate unchanged."""
    try:
        return _check(fn(*args, **kwargs))
    except ServerException as exc:
        if _is_entitlement_error(exc):
            raise MarketDataNotEntitledError(ENTITLEMENT_MESSAGE) from exc
        raise


def get_quote(symbol: str, category: str = Category.US_STOCK.name, *, depth: int | None = None):
    """Latest quote for one symbol."""
    return _call(data_client().market_data.get_quotes, symbol, category, depth=depth)


# Webull's snapshot endpoint rejects more than this many symbols per call with HTTP 417
# "symbols size must be between 1 and 100" (seen nightly from the ~180-name watchlist scan).
SNAPSHOT_MAX_SYMBOLS = 100


def get_snapshot(symbols, category: str = Category.US_STOCK.name, *,
                 extend_hour_required: bool | None = None):
    """Snapshot for one or more symbols (comma string or list per SDK).

    `extend_hour_required=True` adds the pre-market / after-hours block (`extend_hour_last_price`,
    `extend_hour_change_ratio`, `extend_hour_volume`, ...) — the evening watchlist scanner's whole
    input. Left None the flag is NOT sent at all, so every existing caller gets exactly the
    regular-hours row it gets today.
    """
    kwargs = {} if extend_hour_required is None else {"extend_hour_required": extend_hour_required}
    syms = _symbol_list(symbols)
    if len(syms) <= SNAPSHOT_MAX_SYMBOLS:
        return _call(data_client().market_data.get_snapshot, symbols, category, **kwargs)
    # Above the endpoint's cap the whole call 417s ("symbols size must be between 1 and 100"),
    # which silently blanked the evening watchlist scan. Page it and hand back one flat row
    # list; every caller already accepts the list shape.
    out: list = []
    for i in range(0, len(syms), SNAPSHOT_MAX_SYMBOLS):
        raw = _call(data_client().market_data.get_snapshot,
                    ",".join(syms[i:i + SNAPSHOT_MAX_SYMBOLS]), category, **kwargs)
        rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else [])
        out.extend(rows or [])
    return out


def _symbol_list(symbols) -> list[str]:
    """The SDK accepts a comma string or a sequence; normalise to a list of non-empty symbols."""
    if isinstance(symbols, str):
        return [s.strip() for s in symbols.split(",") if s.strip()]
    return [str(s).strip() for s in (symbols or []) if str(s).strip()]


def spot_price(symbol: str) -> float:
    """Latest price for one symbol, from its snapshot. Raises MarketDataNotEntitledError -> 402.
    The single source for the underlying spot used by the options chain/analytics surfaces."""
    snap = get_snapshot(symbol)
    row = snap[0] if isinstance(snap, list) and snap else snap
    return float(row["price"])


def get_bars(symbol: str, timespan: str = Timespan.D.name,
             category: str = Category.US_STOCK.name, count: str = "200"):
    """Historical OHLCV bars. timespan e.g. 'D','M1','M60'; count is a string."""
    return _call(data_client().market_data.get_history_bar, symbol, category, timespan, count=count)
