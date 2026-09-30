"""Candidate discovery — Claude sources its own swing candidates (not the user's watchlist).

The account is Claude-managed, so finding what to trade is the system's job. This module builds the
*hunting ground*: a curated pool of liquid US stocks + broad ETFs, filtered at runtime to the swing
plan's tradable price band via one batch snapshot, then handed to the existing swing screener
(``webull_api.swing.screen``) which applies the real per-symbol rules (trend, pullback, liquidity, ...).

Design notes / why it's shaped this way:
  * **Curated core, not "scan the whole market".** The swing plan is *pullback-in-uptrend*. Sourcing
    only from the day's biggest movers would bias toward momentum gaps — the wrong setup. So the base
    universe is a stable pool of liquid names where pullbacks actually live; fresh in-play names can be
    injected via ``extra_symbols`` (e.g. market movers pulled at the agent layer, since this SDK has no
    movers endpoint).
  * **Band filter here is only a cheap pre-filter.** It drops out-of-band names so we don't waste a
    250-bar history fetch on them. Real *liquidity* (min avg volume / $ volume) is already enforced by
    the planner's Liquidity disqualifier — we deliberately don't duplicate it.
  * **Read-only.** No order path. Discovery finds candidates; drafting/placing happens downstream behind
    the unchanged submit gate.

``MarketDataNotEntitledError`` propagates (callers map it: web -> 402, MCP -> error payload), mirroring
``swing.screen``.
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field

from webull_api import market_data
from webull_api._util import num as _num

# Price keys as the SDK's snapshot rows expose them (same order as the analysis MCP's extractor).
_LAST_KEYS = ("price", "close", "last", "lastPrice")
_SYM_KEYS = ("symbol", "ticker")

# The swing plan's tradable band (see swing/planner.py). Overridable per call.
DEFAULT_MIN_PRICE = 10.0
DEFAULT_MAX_PRICE = 100.0

# How many candidates to hand the screener per run. The screener fetches ~250 daily bars per symbol,
# so this caps run time for the evening cadence. Curated-core names come first, then any injected
# movers — so the cap trims the tail, not the base. Pass ``limit=None`` to screen every in-band
# name (the web setups panel does, on a thread pool — 2026-09-07).
DEFAULT_LIMIT = 40

# The swing plan's per-position concentration rule: whole shares, and no single name above 20% of
# the book (playbook/swing-trading-plan.md §2). The price ceiling therefore tracks the book — a
# $300 stock cannot be sized to a 1% risk on a $400 book — with DEFAULT_MAX_PRICE as the floor so
# a tiny or unknown book never narrows the band below the plan's own example.
BOOK_CEILING_SHARE = 0.20


def band_ceiling(book: float | None) -> float:
    """Max price for the discovery band given the swing book (None/<=0 -> the default ceiling)."""
    if book is None or book <= 0:
        return DEFAULT_MAX_PRICE
    return max(DEFAULT_MAX_PRICE, round(BOOK_CEILING_SHARE * float(book), 2))

# Batch size for snapshot calls (the SDK accepts a list; keep chunks modest to stay well under limits).
_SNAPSHOT_CHUNK = 50

# ── Curated liquid universe ───────────────────────────────────────────────────
# A stable pool of liquid US stocks + broad ETFs that *often* trade in the $10-100 band. Membership is
# intentionally broad and static; the runtime band filter + the planner's liquidity gate do the real
# culling, so a name drifting above the ceiling simply gets skipped that day rather than needing
# maintenance. Grouped by sector only for readability. ~180 names across sectors + broad/sector ETFs.
# Pruned 2026-07-12 (dead tickers 417 their whole snapshot chunk and cost split retries):
# DFS/MRO/X/K/WBA/HOLX gone via acquisition/going-private; GPS renamed GAP; PARA became PSKY.
CURATED_UNIVERSE: tuple[str, ...] = (
    # Financials
    "BAC", "WFC", "C", "USB", "PNC", "TFC", "KEY", "RF", "HBAN", "FITB", "CFG", "SCHW", "MS",
    "SOFI", "ALLY", "SYF", "COF", "MTB", "PYPL",
    # Energy
    "XOM", "CVX", "OXY", "DVN", "APA", "HAL", "SLB", "BKR", "KMI", "WMB", "ET", "CVE",
    "MPC", "VLO", "PSX", "FANG",
    # Industrials / materials
    "GE", "F", "GM", "DAL", "AAL", "UAL", "LUV", "CSX", "NSC", "FCX", "CLF", "NUE", "AA",
    "VALE", "NEM", "GOLD", "BTU", "DE", "EMR",
    # Technology / semis
    "INTC", "AMD", "MU", "CSCO", "HPQ", "HPE", "DELL", "WDC", "STX", "ON", "MRVL", "SWKS", "GLW",
    "NXPI", "PLTR", "UBER", "SNAP", "PINS", "RBLX", "AFRM", "COIN", "HOOD", "DKNG",
    # Communication / media / consumer discretionary
    "T", "VZ", "WBD", "PSKY", "CMCSA", "DIS", "NKE", "SBUX", "MCD", "ABNB", "CCL", "NCLH", "RCL",
    "MGM", "WYNN", "LVS", "EBAY", "ETSY", "GAP", "M", "KSS", "BBY", "TGT", "DG", "DLTR", "ROST",
    # Consumer staples / healthcare
    "KO", "KVUE", "KHC", "MO", "PM", "GIS", "CAG", "CPB", "SYY", "TSN", "HSY",
    "PFE", "MRK", "BMY", "GILD", "CVS", "VTRS", "MRNA", "BAX", "OGN",
    # Utilities / real estate
    "SO", "D", "DUK", "EXC", "AEP", "PCG", "NRG", "ED", "PPL", "CNP", "AES", "KIM", "O", "VICI",
    # High-liquidity retail names
    "PLUG", "RIVN", "LCID", "NIO", "CHPT", "RUN", "FCEL", "BB", "NOK", "SIRI", "GRAB", "MARA",
    "RIOT", "CLSK", "AI", "SOUN", "IONQ", "ACHR", "JOBY", "SMCI", "WOLF",
    # Broad + sector ETFs (band filter drops any that run above the ceiling)
    "XLF", "XLE", "XLI", "XLU", "XLV", "XLP", "XLY", "XLB", "XLRE", "XLC", "KRE", "KWEB", "EEM",
    "EWZ", "GDX", "SLV", "XOP", "SMH", "ARKK", "HYG", "TLT",
)


@dataclass
class DiscoverResult:
    """The screener-ready candidate list + diagnostics for a discovery run."""
    symbols: list[str] = field(default_factory=list)   # in-band, deduped, capped, core-first
    scanned: int = 0                                    # universe size probed (curated + extras, deduped)
    in_band: int = 0                                    # count passing the band filter (before the cap)
    no_price: list[str] = field(default_factory=list)   # symbols the snapshot returned no usable price for


def _price_of(row: dict) -> float | None:
    return _num(row, _LAST_KEYS)


def _rows(raw) -> list:
    """Snapshot list, or a ``{"data": [...]}`` envelope, -> list of rows (defensive)."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return []


def _prices_by_symbol(raw) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in _rows(raw):
        if not isinstance(row, dict):
            continue
        sym = next((row[k] for k in _SYM_KEYS if isinstance(row.get(k), str) and row[k]), "")
        price = _price_of(row)
        if sym and price is not None:
            out[sym.strip().upper()] = price
    return out


def _dedup_upper(symbols) -> list[str]:
    """Uppercase, strip, drop blanks, preserve first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for s in symbols:
        u = (s or "").strip().upper()
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out


_FETCH_WORKERS = 8


def _fetch_prices(symbols: list[str], get_snapshot) -> dict[str, float]:
    """Batch snapshot -> {SYMBOL: price}, resilient to the SDK's all-or-nothing snapshot.

    ``get_snapshot`` is all-or-nothing: one unrecognized/delisted ticker in a batch 417s the WHOLE
    batch. Batching naively would let a single bad ticker silently drop its 50 healthy neighbours. So
    on any non-entitlement failure the batch splits and each half retries — a bad ticker isolates
    down to itself (yielding no price, i.e. it just falls into ``no_price``) instead of taking its
    chunk with it. Batches (and their split halves) run on a small thread pool: the sequential
    version cost O(dead·log(chunk)) round-trips — ~70 s live when 8 curated names died via
    corporate actions. Entitlement errors propagate (callers map them upstream); the result dict is
    keyed by symbol, so completion order can't change the outcome.
    """
    out: dict[str, float] = {}
    batches = [symbols[i:i + _SNAPSHOT_CHUNK] for i in range(0, len(symbols), _SNAPSHOT_CHUNK)]
    if not batches:
        return out
    with ThreadPoolExecutor(max_workers=_FETCH_WORKERS) as ex:
        pending = {ex.submit(get_snapshot, batch): batch for batch in batches}
        while pending:
            done, _ = wait(set(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                batch = pending.pop(fut)
                try:
                    raw = fut.result()
                except market_data.MarketDataNotEntitledError:
                    for f in pending:
                        f.cancel()          # queued work is pointless; running calls just finish
                    raise
                except Exception:
                    if len(batch) > 1:      # split; a lone unpriceable symbol is simply dropped
                        mid = len(batch) // 2
                        for half in (batch[:mid], batch[mid:]):
                            pending[ex.submit(get_snapshot, half)] = half
                    continue
                out.update(_prices_by_symbol(raw))
    return out


def discover(
    universe=CURATED_UNIVERSE,
    *,
    extra_symbols=(),
    min_price: float = DEFAULT_MIN_PRICE,
    max_price: float = DEFAULT_MAX_PRICE,
    limit: int | None = DEFAULT_LIMIT,
    get_snapshot=market_data.get_snapshot,
) -> DiscoverResult:
    """Build the screener-ready candidate list from the curated universe (+ any injected movers).

    Batches a snapshot over the pool, keeps names whose last price is within ``[min_price, max_price]``,
    dedupes (curated-core first, then extras), and caps at ``limit`` (None = no cap). Liquidity is NOT filtered here —
    the planner's Liquidity disqualifier owns that. ``get_snapshot`` is injectable for tests.
    """
    pool = _dedup_upper(list(universe) + list(extra_symbols))
    result = DiscoverResult(scanned=len(pool))
    if not pool:
        return result

    # Entitlement errors propagate (mapped upstream); a bad ticker isolates to itself via the
    # recursive-split fallback rather than dropping its whole chunk (all-or-nothing snapshot).
    prices = _fetch_prices(pool, get_snapshot)

    in_band: list[str] = []
    for sym in pool:                                   # iterate the pool to preserve core-first order
        p = prices.get(sym)
        if p is None:
            result.no_price.append(sym)
        elif min_price <= p <= max_price:
            in_band.append(sym)

    result.in_band = len(in_band)
    result.symbols = list(in_band) if limit is None else in_band[:limit]
    return result
