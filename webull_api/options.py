"""Read-only options market data: snapshot / bars / tick.

Mirrors market_data.py's entitlement handling (the 'please subscribe' 401 →
MarketDataNotEntitledError) and adds InvalidOptionSymbolError for the all-or-nothing
417 invalid-symbol batch error, carrying the parsed invalid-symbol list so the chain
orchestrator can prune and retry. Needs the OPRA (US_OPTION) entitlement.
"""
from __future__ import annotations

from webull.core.exception.exceptions import ServerException
from webull.data.common.category import Category

from .client import data_client
from .market_data import MarketDataNotEntitledError, _is_entitlement_error
from .options_chain import parse_invalid_symbols

_CAT = Category.US_OPTION.name


class InvalidOptionSymbolError(RuntimeError):
    """The snapshot batch contained option symbols the API rejects (417 INVALID_SYMBOL).

    All-or-nothing: the whole request fails, but the error lists the bad symbols.
    `.invalid` is that set, so the caller can drop them and retry the survivors.
    """

    def __init__(self, invalid: set[str]):
        super().__init__(f"invalid option symbols: {sorted(invalid)}")
        self.invalid = invalid


def _is_invalid_symbol(exc: ServerException) -> bool:
    if getattr(exc, "error_code", "") == "INVALID_SYMBOL":
        return True
    return "invalid symbol" in (getattr(exc, "error_msg", "") or "").lower()


def _check(res):
    if res.status_code != 200:
        raise RuntimeError(f"option data error {res.status_code}: {res.text}")
    return res.json()


def _call(fn, *args, **kwargs):
    try:
        return _check(fn(*args, **kwargs))
    except ServerException as exc:
        if _is_entitlement_error(exc):
            raise MarketDataNotEntitledError(
                "Options market data not subscribed. Claim OPRA Real-Time (Non-display) "
                "under Webull OpenAPI → Advanced Quotes."
            ) from exc
        if _is_invalid_symbol(exc):
            raise InvalidOptionSymbolError(parse_invalid_symbols(getattr(exc, "error_msg", "") or "")) from exc
        raise


def get_option_snapshot(symbols):
    """Latest snapshot(s) for option contract(s) (OCC symbol csv or list; ≤20 per call)."""
    return _call(data_client().option_market_data.get_option_snapshot, symbols, _CAT)


def get_option_bars(symbol, timespan: str = "D", count: str = "120"):
    """Historical bars for one contract. timespan is an UPPERCASE enum (M1,M5,…,D,W,M,Y)."""
    return _call(data_client().option_market_data.get_option_history_bars, symbol, _CAT, timespan, count=count)


def get_option_tick(symbol, count: str = "30"):
    """Recent trades (time & sales) for one contract."""
    return _call(data_client().option_market_data.get_option_tick, symbol, _CAT, count=count)
