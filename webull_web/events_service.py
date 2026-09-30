"""Earnings watch on HELD names. The entry gate checks earnings once, at entry
(swing/screen.py); this is the standing re-check while a name is held, across every
book (real + paper sleeves — the sleeves feed the proof sample). Pure `assess` is
unit-tested; impure `held_earnings` gathers held symbols with each book independently
degradable — an unreadable book is NAMED in the result, never silently dropped.
Read-only: broker positions via webull_api.portfolio only, no trading import."""
from __future__ import annotations

import logging
from datetime import date

_log = logging.getLogger(__name__)

WINDOW_DAYS = 14  # matches the swing planner's earnings disqualifier window

# get_positions() rows are broker JSON — the same tolerant key sets reconcile.py uses.
_SYMBOL_KEYS = ("symbol", "ticker")
_QTY_KEYS = ("quantity", "qty", "position", "shares")


def assess(symbols_by_book: dict[str, list[str]], fetch, today: str,
           window_days: int = WINDOW_DAYS) -> dict:
    """Pure. fetch(symbol) -> (date | None, checked). Symbols dedup across books; a hit
    lists every book holding the name (insertion order). days = CALENDAR days until
    earnings; a hit needs 0 <= days <= window_days. With no working fetch every symbol
    lands in `unverified` and hits stay empty — honest by construction, no special case."""
    books_of: dict[str, list[str]] = {}
    for book, syms in symbols_by_book.items():
        for s in syms:
            sym = str(s or "").strip().upper()
            if sym and book not in books_of.setdefault(sym, []):
                books_of[sym].append(book)
    hits: list[dict] = []
    unverified: list[str] = []
    for sym in sorted(books_of):
        d, checked = fetch(sym)
        if not checked:
            unverified.append(sym)
            continue
        if not d:
            continue
        try:
            days = (date.fromisoformat(str(d)[:10]) - date.fromisoformat(today)).days
        except ValueError:
            unverified.append(sym)  # a date we can't parse is a claim we can't verify
            continue
        if 0 <= days <= window_days:
            hits.append({"symbol": sym, "books": books_of[sym],
                         "date": str(d)[:10], "days": days})
    hits.sort(key=lambda h: (h["days"], h["symbol"]))
    return {"window_days": window_days, "checked": len(books_of),
            "unverified": unverified, "hits": hits}


def _real_symbols() -> list[str]:
    from webull_api import portfolio

    from . import runner_util
    syms: list[str] = []
    for a in runner_util.retry_throttled(portfolio.list_accounts) or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("account_id") or a.get("id")
        if not aid:
            continue
        raw = runner_util.retry_throttled(lambda aid=aid: portfolio.get_positions(str(aid)))
        for pos in (raw if isinstance(raw, list) else []):
            if not isinstance(pos, dict):
                continue
            if str(pos.get("instrument_type") or pos.get("asset_type") or "").upper() == "OPTION":
                continue
            sym = next((str(pos[k]) for k in _SYMBOL_KEYS if pos.get(k)), "")
            qty = next((pos[k] for k in _QTY_KEYS if pos.get(k) is not None), None)
            try:
                if sym and qty is not None and float(qty) > 0:
                    syms.append(sym)
            except (TypeError, ValueError):
                continue
    return syms


def _rsi2_symbols() -> list[str]:
    from . import rsi2_store
    return [str(lot["symbol"]) for lot in (rsi2_store.load().get("owned_lots") or [])
            if lot.get("symbol")]


def _paper_equity_symbols() -> list[str]:
    from . import paper_store
    positions = (paper_store.load() or {}).get("positions") or {}
    out: list[str] = []
    for sym, pos in (positions.items() if isinstance(positions, dict) else []):
        try:
            q = float((pos or {}).get("qty", (pos or {}).get("quantity", 0)) or 0)
        except (TypeError, ValueError):
            q = 0.0
        if q > 0:
            out.append(str(sym))
    return out


def _paper_option_underlyings() -> list[str]:
    from . import paper_options_service as opt_svc
    return sorted(opt_svc.account_underlyings(opt_svc.load_account()))


def _proven_symbols() -> list[str]:
    from . import proven_store
    return [str(s) for s in (proven_store.load() or {})]


_BOOK_SOURCES = [("real", _real_symbols), ("rsi2", _rsi2_symbols),
                 ("paper-eq", _paper_equity_symbols),
                 ("paper-opt", _paper_option_underlyings), ("proven", _proven_symbols)]


def held_earnings(today: str) -> dict:
    """All-books earnings view for the manager's note. A book whose reader raises lands
    in books_unavailable by name; the rest are still checked."""
    from . import news
    symbols_by_book: dict[str, list[str]] = {}
    books_unavailable: list[str] = []
    for book, src in _BOOK_SOURCES:
        try:
            symbols_by_book[book] = src()
        except Exception:
            _log.exception("events: %s book unreadable", book)
            books_unavailable.append(book)
    out = assess(symbols_by_book, news.get_next_earnings_checked, today)
    out["books_unavailable"] = books_unavailable
    return out
