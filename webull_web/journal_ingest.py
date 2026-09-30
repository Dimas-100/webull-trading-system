"""Pull-based, idempotent sync: read the authoritative sources, normalize, append new records.
The only journal module with I/O. Reads orders via the READ-ONLY trading.get_order_history —
never the order gate."""
from __future__ import annotations

from typing import Callable

from webull_api import market_data, trading
from webull_api.analysis import support_resistance, technicals
from webull_api.journal import normalize
from webull_api.journal.schema import MarketContext
from webull_api.strategy.bars import to_ohlcv
from webull_web import journal_store, paper_options_service, paper_store, practice_store


def _context_at(symbol: str, when_iso: str, cache: dict,
                bars_fn: Callable = market_data.get_bars) -> MarketContext | None:
    try:
        if symbol not in cache:
            cache[symbol] = to_ohlcv(bars_fn(symbol, "D", count="250"))
        bars = cache[symbol]
        if not bars:
            return None
        day = when_iso[:10]
        idx = -1
        for j, b in enumerate(bars):
            if str(b.get("time", ""))[:10] <= day:
                idx = j
            else:
                break
        if idx < 0:
            return None
        sub = bars[: idx + 1]
        t = technicals(sub)
        sr = support_resistance(sub)
        return MarketContext(as_of_iso=sub[-1].get("time"), trend=t["trend"], rsi14=t["rsi14"],
                             sma20=t["sma20"], sma50=t["sma50"], pct_change_5=t["pct_change_5"],
                             support=sr["support"], resistance=sr["resistance"])
    except Exception:
        return None


def _ctx_fn(bars_fn: Callable) -> Callable:
    cache: dict = {}

    def ctx(symbol: str, when: str) -> MarketContext | None:
        return _context_at(symbol, when, cache, bars_fn=bars_fn)

    return ctx


def sync_real(account_ids: list[str], *,
              history_fn: Callable = trading.get_order_history,
              bars_fn: Callable = market_data.get_bars) -> tuple[int, str | None]:
    """Append filled real orders (read-only via get_order_history) for each account. Returns
    (appended, error). Skips the market-context fetch for fills already journaled."""
    ctx = _ctx_fn(bars_fn)
    n = 0
    try:
        skip = journal_store.existing_fill_ids()
        for aid in account_ids:
            raw = history_fn(aid)
            fills = normalize.fills_from_order_history(raw, aid, context_fn=ctx, skip_ids=skip)
            n += journal_store.append_fills(fills)
        return n, None
    except Exception as e:  # noqa: BLE001 - degrade; keep the partial count
        return n, str(e)


def sync_paper(*, paper_load: Callable = paper_store.load,
               bars_fn: Callable = market_data.get_bars) -> tuple[int, str | None]:
    """Append filled equity paper orders. Returns (appended, error)."""
    ctx = _ctx_fn(bars_fn)
    try:
        acct = paper_load()
        if not acct:
            return 0, None
        skip = journal_store.existing_fill_ids()
        fills = normalize.fills_from_paper_account(acct, context_fn=ctx, skip_ids=skip)
        return journal_store.append_fills(fills), None
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def sync_options_paper(*, options_load: Callable = paper_options_service.load_account
                        ) -> tuple[int, str | None]:
    """Append closed options-paper trades (open->close / open->expiration) to the Journal.
    Returns (appended, error). Skips trades already journaled (dedup by close-event id)."""
    try:
        acct = options_load()
        if not acct:
            return 0, None
        skip = journal_store.existing_option_trade_ids()
        trades = normalize.option_trades_from_options_account(acct, skip_ids=skip)
        return journal_store.append_option_trades(trades), None
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def sync_practice(*, practice_list: Callable = practice_store.list_sessions,
                  practice_get: Callable = practice_store.get_session) -> tuple[int, str | None]:
    """Append practice take/skip decisions from finished sessions. Returns (appended, error)."""
    n = 0
    try:
        for meta in practice_list():
            sess = practice_get(meta["id"])
            decisions = normalize.decisions_from_session(sess)
            n += journal_store.append_decisions(decisions)
        return n, None
    except Exception as e:  # noqa: BLE001 - degrade; keep the partial count
        return n, str(e)


def sync_all(account_ids: list[str], *,
             history_fn: Callable = trading.get_order_history,
             paper_load: Callable = paper_store.load,
             options_load: Callable = paper_options_service.load_account,
             practice_list: Callable = practice_store.list_sessions,
             practice_get: Callable = practice_store.get_session,
             bars_fn: Callable = market_data.get_bars) -> tuple[dict, dict]:
    """Reconcile every source. Composition of the per-source syncs; returns ({real,paper,options,
    practice} counts, {source: error}) — the contract the old web /api/journal/sync route used (route removed 2026-09-13; journal_ingest is called directly now)."""
    appended = {"real": 0, "paper": 0, "options": 0, "practice": 0}
    errors: dict[str, str] = {}

    for key, (n, err) in (
        ("real", sync_real(account_ids, history_fn=history_fn, bars_fn=bars_fn)),
        ("paper", sync_paper(paper_load=paper_load, bars_fn=bars_fn)),
        ("options", sync_options_paper(options_load=options_load)),
        ("practice", sync_practice(practice_list=practice_list, practice_get=practice_get)),
    ):
        appended[key] = n
        if err is not None:
            errors[key] = err

    return appended, errors
