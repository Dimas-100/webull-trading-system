"""Today's-setups orchestrator: source candidates — Claude's discovery universe by default, or an
explicitly requested owner watchlist (operator escape hatch) — fetch bars/snapshot/SPY/earnings,
run the pure scanner, return ranked setups. Read-only; never imports trading.
Entitlement -> 402 (propagates)."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

from webull_api import discovery, market_data, portfolio, setups, watchlist
from webull_api._util import num as _num  # canonical defensive numeric extractor
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.strategy.bars import to_ohlcv
from webull_api.swing.planner import plan_swing
from webull_api.tiingo import bars as tiingo_bars, store as tiingo_store

from . import news
from .tracks import DAY_CARVE_OUT


STORE_FRESH_DAYS = 4       # the store's last bar must be this recent (a weekend plus one holiday)
STORE_BARS = "1250"        # ~5 years from the offline Tiingo store; the Webull fallback keeps 250
_store_get_bars = tiingo_bars.make_get_bars()   # get_bars-shaped, adjusted, oldest-first


def _store_is_fresh(symbol: str, today: date) -> bool:
    """True when the broad Tiingo store holds the last completed session for `symbol`."""
    last = tiingo_store.last_date(symbol)
    if not last:
        return False
    try:
        return (today - date.fromisoformat(last)).days <= STORE_FRESH_DAYS
    except ValueError:
        return False


def _daily_bars(sym: str, today: date, count: str = "250") -> list[dict]:
    """Daily bars for the screen: the broad Tiingo store first (deep, adjusted, offline, no rate
    limit) when it is fresh, otherwise the Webull fetch exactly as before. Both paths go through
    to_ohlcv, so the shape is identical downstream (2026-09-08 swing screen on the broad store)."""
    if _store_is_fresh(sym, today):
        try:
            return to_ohlcv(_store_get_bars(sym, "D", count=STORE_BARS))
        except (KeyError, ValueError):
            pass
    return to_ohlcv(market_data.get_bars(sym, "D", count=count))


def _best_effort(fn):
    try:
        return fn()
    except MarketDataNotEntitledError:
        raise
    except Exception:
        return None


def _resolve(watchlist_id, name, lists):
    if watchlist_id:
        m = next((w for w in lists if str(w.get("id")) == str(watchlist_id)), None)
        if m:
            return m
    if name:
        m = next((w for w in lists if (w.get("name") or "").strip().lower() == name.strip().lower()), None)
        if m:
            return m
    for w in lists:
        if (w.get("name") or "").strip().lower() == "my watchlist":
            return w
    return lists[0] if lists else None


def _snapshots(syms):
    """{UPPER_SYMBOL: {'last', 'changePct'}} from a batched snapshot; {} on any error."""
    try:
        raw = market_data.get_snapshot(",".join(syms))
        rows = raw if isinstance(raw, list) else (raw.get("data") if isinstance(raw, dict) else [])
        out = {}
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            sym = next((str(r[k]) for k in ("symbol", "ticker") if r.get(k)), "")
            if not sym:
                continue
            last = _num(r, ["price", "close", "last"])
            prev = _num(r, ["pre_close", "preClose", "prevClose"])
            ratio = _num(r, ["change_ratio", "changeRatio"])
            if ratio is not None:
                change_pct = ratio * 100
            elif last is not None and prev not in (None, 0):
                change_pct = (last / prev - 1) * 100
            else:
                change_pct = None
            out[sym.upper()] = {"last": last, "changePct": change_pct}
        return out
    except Exception:
        return {}


def _watchlist_source(watchlist_id, name):
    """(symbols, source, watchlist) for an explicitly requested owner watchlist."""
    lists = watchlist.get_watchlists()
    target = _resolve(watchlist_id, name, lists)
    if not target:
        return [], {"kind": "watchlist", "id": None, "name": None}, None
    sym_rows = watchlist.get_watchlist_symbols(target["id"])
    syms = [r["symbol"] for r in sym_rows if r.get("symbol")]
    wl = {"id": target["id"], "name": target.get("name")}
    return syms, {"kind": "watchlist", **wl}, wl


def swing_book() -> float | None:
    """The real swing book: the INDIVIDUAL_CASH account's net liquidation minus the real
    day-trading carve-out (tracks.DAY_CARVE_OUT, 0 since the hand book was retired 2026-09-21).
    None when the broker can't be read — the band then falls back to its default ceiling rather
    than guessing. Never accounts[0]: that is the Crypto account."""
    try:
        accts = portfolio.list_accounts()
        aid = next((a.get("account_id") for a in (accts if isinstance(accts, list) else [])
                    if a.get("account_class") == "INDIVIDUAL_CASH"), None)
        if not aid:
            return None
        bal = portfolio.get_balance(aid) or {}
        nlv = _num(bal, ["total_net_liquidation_value", "net_liquidation_value"])
        if nlv is None:
            return None
        return round(max(float(nlv) - float(DAY_CARVE_OUT), 0.0), 2)
    except MarketDataNotEntitledError:
        raise
    except Exception:
        return None


def _discovery_source(book: float | None):
    """(symbols, source, None) from Claude's curated discovery pool: band-filtered with the ceiling
    tied to the live swing book (discovery.band_ceiling), and UNCAPPED — every in-band name is
    screened (the scan runs on a thread pool, so 98 names cost seconds, not minutes)."""
    max_price = discovery.band_ceiling(book)
    found = discovery.discover(max_price=max_price, limit=None)
    return found.symbols, {"kind": "discovery", "universe": found.scanned,
                           "in_band": found.in_band, "limit": None,
                           "min_price": discovery.DEFAULT_MIN_PRICE, "max_price": max_price,
                           "book": book}, None


_SCAN_WORKERS = 8


def _scan_one(sym, *, spy, snaps, today, book=None):
    """(row, error) for one symbol — exactly one is non-None. Entitlement propagates."""
    try:
        daily = _daily_bars(sym, today)
    except MarketDataNotEntitledError:
        raise
    except Exception as e:
        return None, {"symbol": sym, "error": type(e).__name__}
    if not daily:
        return None, {"symbol": sym, "error": "no bars"}
    earnings = _best_effort(lambda s=sym: news.get_next_earnings_date(s))
    # confirms assumed True (like swing/screen.py) — the two manual checks (leveraged ETF /
    # binary event) can't be automated, and leaving them False makes PASS unreachable
    # Sized to the live swing book when known, so the plan's own price ceiling and share count
    # agree with the band the candidates were drawn from.
    plan_kw = {"book": float(book)} if book else {}
    plan = _best_effort(lambda d=daily, s=sym, e=earnings: plan_swing(
        s, d, spy=spy, earnings_date=e,
        confirm_not_leveraged=True, confirm_not_binary=True, **plan_kw).model_dump())
    snap = snaps.get(sym.upper()) or {}
    row = setups.scan_symbol(sym, daily, spy_bars=spy, swing_plan=plan,
                             earnings_date=earnings, today=today, last=snap.get("last"))
    row["change_pct"] = snap.get("changePct")
    return row, None


def scan_setups(watchlist_id: str | None = None, name: str | None = None) -> dict:
    """Ranked setups over Claude's discovery universe (default) or an explicitly named watchlist.

    The managed account is Claude-run, so the default hunting ground is the same Claude-curated
    discovery pool the evening scan and autopilot use — the owner's watchlists are scanned only
    when one is explicitly requested."""
    book = None
    if watchlist_id or name:
        syms, source, wl = _watchlist_source(watchlist_id, name)
    else:
        book = swing_book()
        syms, source, wl = _discovery_source(book)
    snaps = _snapshots(syms) if syms else {}
    today = date.today()
    spy = _best_effort(lambda: _daily_bars("SPY", today))
    rows, errors, scanned = [], [], 0
    if syms:
        # Bounded parallelism: the 40-name discovery default took ~2.5 min live when the
        # per-symbol bar + earnings fetches ran sequentially. executor.map keeps input order
        # (deterministic rows/errors) and re-raises a worker's MarketDataNotEntitledError. The
        # worker count is the pacing: eight in flight keeps the request rate the same whether
        # 40 or 98 names are screened — only the wall-clock grows.
        with ThreadPoolExecutor(max_workers=min(_SCAN_WORKERS, len(syms))) as ex:
            results = list(ex.map(lambda s: _scan_one(s, spy=spy, snaps=snaps, today=today, book=book), syms))
    else:
        results = []
    for row, err in results:
        if err is not None:
            errors.append(err)
        else:
            rows.append(row)
            scanned += 1
    return {"source": source, "watchlist": wl,
            "setups": setups.rank(rows), "scanned": scanned, "errors": errors}
