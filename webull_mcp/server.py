"""FastMCP server exposing read-only option market-data tools backed by webull_api.

The pure tool logic lives in `option_expirations`/`option_chain`/`option_snapshot` (plain
functions returning JSON strings, unit-tested); the `get_option_*` wrappers register them as MCP
tools. Every call is wrapped by `_safe` so a failure returns an error payload instead of crashing
the stdio server. NO order/trading import — read-only by construction.
"""
from __future__ import annotations

from datetime import date

from mcp.server.fastmcp import FastMCP

from webull_api import market_data, options, options_analytics, options_chain, options_events, options_income, options_surface
from webull_api.market_data import MarketDataNotEntitledError
from webull_mcp.safe import safe as _shared_safe

mcp = FastMCP("webull-options")

_ENTITLE_MSG = (
    "Options market data not subscribed. Claim OPRA Real-Time (Non-display) in "
    "Webull OpenAPI -> Advanced Quotes."
)


def _safe(fn) -> str:
    """Run fn(); return a JSON string (error payload on failure) — never raises."""
    return _shared_safe(fn, handlers=[
        (MarketDataNotEntitledError, lambda e: {"error": "MarketDataNotEntitled", "message": _ENTITLE_MSG}),
    ])


# ── pure-ish tool logic (returns JSON strings; unit-tested) ───────────────────

def option_expirations(symbol: str) -> str:
    def go():
        spot = market_data.spot_price(symbol)
        return {
            "symbol": symbol.upper(),
            "spot": spot,
            "expirations": options_chain.discover_expirations(symbol, spot, date.today()),
        }

    return _safe(go)


def option_chain(symbol: str, expiration: str, width: int = 12) -> str:
    def go():
        spot = market_data.spot_price(symbol)
        return options_chain.fetch_chain(symbol, date.fromisoformat(expiration), spot, width)

    return _safe(go)


def option_snapshot(occ_symbols: str) -> str:
    return _safe(lambda: options.get_option_snapshot(occ_symbols))


def option_analytics_expected_move(symbol: str, expiration: str) -> str:
    return _safe(lambda: options_analytics.expected_move_for(symbol, expiration))


def option_analytics_strategy(symbol: str, expiration: str, legs: list) -> str:
    return _safe(lambda: options_analytics.analyze_for(symbol, expiration, legs))


def option_vol_surface(symbol: str) -> str:
    return _safe(lambda: options_surface.vol_surface_for(symbol))


def option_income(symbol: str, expiration: str, side: str) -> str:
    return _safe(lambda: options_income.income_for(symbol, expiration, side))


def option_earnings_move(symbol: str) -> str:
    return _safe(lambda: options_events.earnings_move_for(symbol))


# ── MCP tool registration ─────────────────────────────────────────────────────

@mcp.tool()
def get_option_expirations(symbol: str) -> str:
    """List live option expiration dates (ISO YYYY-MM-DD) for an underlying US stock/ETF symbol,
    plus the current spot price. Example: symbol="AAPL"."""
    return option_expirations(symbol)


@mcp.tool()
def get_option_chain(symbol: str, expiration: str, width: int = 12) -> str:
    """Option chain for an underlying + expiration (YYYY-MM-DD). Returns strikes paired into
    call/put with last price, bid/ask, implied volatility, greeks (delta/gamma/theta/vega/rho),
    open interest and volume. `width` = number of strikes each side of ATM (default 12)."""
    return option_chain(symbol, expiration, width)


@mcp.tool()
def get_option_snapshot(occ_symbols: str) -> str:
    """Latest quote for specific option contracts by OCC symbol, comma-separated (<=20),
    e.g. "AAPL260717C00295000,AAPL260717P00300000"."""
    return option_snapshot(occ_symbols)


@mcp.tool()
def get_option_analytics(symbol: str, expiration: str) -> str:
    """Expected move (1-sigma price range and %), ATM implied vol, and best-effort IV-vs-HV for an
    underlying + expiration (YYYY-MM-DD). Example: symbol="AAPL", expiration="2026-07-17"."""
    return option_analytics_expected_move(symbol, expiration)


@mcp.tool()
def analyze_option_strategy(symbol: str, expiration: str, legs: list) -> str:
    """Breakevens, probability of profit, max profit/loss, risk:reward and net greeks for a single
    or vertical option strategy. legs = [{"symbol": OCC, "side": "BUY"|"SELL", "quantity": "1"}]
    (1 leg = single, 2 legs = vertical). Also returns expected value, probability-of-touch, a
    price/IV scenario grid, and a liquidity score."""
    return option_analytics_strategy(symbol, expiration, legs)


@mcp.tool()
def get_vol_surface(symbol: str) -> str:
    """Implied-vol skew (IV across strikes) + term structure (ATM IV across expirations) for an
    underlying US stock/ETF. Shows which strikes/expirations are rich vs cheap."""
    return option_vol_surface(symbol)


@mcp.tool()
def get_income_yields(symbol: str, expiration: str, side: str = "call") -> str:
    """Covered-call (side="call") or cash-secured-put (side="put") annualized yields per strike for
    an underlying + expiration (YYYY-MM-DD), with downside cushion / effective discount + breakeven."""
    return option_income(symbol, expiration, side)


@mcp.tool()
def get_earnings_move(symbol: str) -> str:
    """Options-implied move around the next earnings date vs the stock's historical post-earnings
    moves (verdict: options expensive/cheap/fair). Best-effort historical comparison."""
    return option_earnings_move(symbol)
