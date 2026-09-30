"""RSI2 paper runner — impure shell. The ONLY I/O layer: fetch bars/snapshots, load the paper account
and the ownership ledger, reconcile, queue next-open orders via the shared paper engine (settled at
the next evening's run; the ledger adopts verified fills at settle), record to the ledger + run-log +
action-log. 100% paper — no `trading` import, no real-order path. Mirrors the
Lab Cycle's lock + same-day-guard + degrade-per-source discipline."""
from __future__ import annotations

import logging
from datetime import datetime

from webull_api import action_log, market_data, run_log
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.paper import engine as paper_engine
from webull_api.strategy import rsi2
from webull_api.strategy.bars import to_ohlcv

from . import paper_service, paper_settle_service, runner_util

_log = logging.getLogger(__name__)

CFG = rsi2.DEFAULT_CONFIG           # module-level so tests can shrink the universe


def _bar_date(bar_time) -> str | None:
    """The bar's calendar date (YYYY-MM-DD). Handles ISO strings + epoch sec/ms (mirrors lab/trial)."""
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


def _fresh_rsi(today_et: str, symbols, errors: list) -> dict:
    """Latest RSI(2) per symbol, only where the newest daily bar is dated today ET."""
    out: dict = {}
    for sym in symbols:
        try:
            bars = to_ohlcv(market_data.get_bars(sym, "D", count="30"))
        except MarketDataNotEntitledError:
            raise
        except Exception:
            errors.append(f"{sym}: bar fetch failed")
            continue
        if not bars or _bar_date(bars[-1]["time"]) != today_et:
            continue
        r = rsi2.rsi2_of([b["close"] for b in bars])
        if r is not None:
            out[sym] = r
    return out


def _next_earnings_checked(sym: str) -> tuple[str | None, bool]:
    """(next confirmed earnings date ISO, checked) within 21 days — OBSERVATIONAL only
    (spec 2026-08-03): recorded on the entry's action row, never a gate. Lazy import +
    best-effort (mirrors options_entry_service._next_earnings): an earnings-source failure
    must never fail, delay, or alter the run. (None, False) = unchecked, NOT verified-clear."""
    try:
        from . import news
        return news.get_next_earnings_checked(sym, ahead_days=21)
    except Exception:
        return None, False


def run(force: bool = False) -> dict:
    from . import rsi2_store
    iso, today = paper_service.now_et()
    with runner_util.filelock("rsi2", iso) as got:
        if not got:
            return {"result": "no_op", "placed": 0, "exits": 0, "entries": 0, "errors": [],
                    "summary": "RSI2: another run in progress", "ran_at": iso}
        if runner_util.already_ran_today("rsi2", today, force):
            return {"result": "no_op", "placed": 0, "exits": 0, "entries": 0, "errors": [],
                    "summary": "RSI2: already ran today", "ran_at": iso}

        errors: list = []
        try:
            settle_res = paper_settle_service.settle_book()
        except MarketDataNotEntitledError:
            raise
        except Exception as e:
            settle_res = {"settled": [], "rejected": [], "resting": [], "stale": [],
                          "errors": [f"settle failed: {type(e).__name__}"]}
        errors.extend(settle_res["errors"])

        acct = paper_service.load_account()          # post-settle view of the book
        state = rsi2_store.load()
        orders_by_id = {o.paper_order_id: o
                        for o in list(acct.history) + list(acct.open_orders)}
        state, _adopt_notes = rsi2_store.adopt_settled(state, orders_by_id, today)
        state, _notes = rsi2_store.reconcile(state, acct, today)

        owned_syms = [l["symbol"] for l in state.get("owned_lots", [])]
        symbols = list(dict.fromkeys(list(CFG.universe) + owned_syms))
        rsi_by_symbol = _fresh_rsi(today, symbols, errors)
        if not rsi_by_symbol:
            rsi2_store.save(state, iso)
            if errors:
                # No fresh RSI *because bar fetches failed* — a broker/token/network outage, NOT a
                # holiday. Report it as an error (exit 1, red on the Monitor) so a blind run never
                # masquerades as a healthy no-op.
                summary = f"RSI2: market data unavailable — {len(errors)} fetch error(s)"
                result = "error"
            else:
                summary = "RSI2: no fresh daily bar (market holiday?) — no-op"
                result = "no_op"
            if settle_res["settled"]:
                summary += f" · settled {len(settle_res['settled'])}"
            if settle_res["stale"]:
                summary += f" · ⚠ {len(settle_res['stale'])} stale queued order(s)"
                result = "error"
            run_log.append([{"key": "rsi2", "ts": iso, "result": result, "summary": summary,
                             "placed": 0, "errors": errors}])
            return {"result": result, "placed": 0, "exits": 0, "entries": 0, "errors": errors,
                    "summary": summary, "ran_at": iso}

        try:
            prices = paper_service.last_prices(symbols)  # owned ∪ universe, so an out-of-universe lot can still exit
        except MarketDataNotEntitledError:
            raise
        except Exception:
            prices = {}

        decisions = rsi2.decide(owned_lots=state.get("owned_lots", []),
                                cash=paper_engine.buying_power(acct),
                                rsi_by_symbol=rsi_by_symbol, price_by_symbol=prices, cfg=CFG)
        # decide() is queue-blind: a queued next-open order is in neither owned_lots nor
        # positions, so a settle-failure day (or a --force rerun) would re-signal the same
        # symbol and double-queue it. An in-flight symbol takes no new decision either side.
        in_flight = ({p.get("symbol") for p in state.get("pending_orders", [])}
                     | {o.symbol for o in acct.open_orders if o.fill_policy == "next_open"})
        decisions = [d for d in decisions if d["symbol"] not in in_flight]

        placed = exits = entries = 0
        for d in decisions:
            sym, side = d["symbol"], d["action"]
            try:
                # thesis is a structured ThesisRecord|None on the paper order; the human "why"
                # rides the action-log entry below (ref==paper_order_id), which the Monitor joins.
                acct, order = paper_engine.place_order(
                    acct, symbol=sym, side=side, order_type="MARKET", quantity=d["quantity"],
                    limit_price=None, time_in_force="DAY", now_iso=iso, today_et=today,
                    last_price=prices.get(sym), thesis=None, fill_policy="next_open")
            except Exception as e:
                errors.append(f"{side} {sym}: {type(e).__name__}: {e}")
                continue
            rsi2_store.record_pending(state, paper_order_id=order.paper_order_id,
                                      symbol=sym, side=side, placed_date=today)
            if side == "BUY":
                entries += 1
            else:
                exits += 1
            placed += 1
            row = {"id": order.paper_order_id, "ts": iso,
                   "kind": "exit" if side == "SELL" else "trade",
                   "sleeve": "paper-equity", "symbol": sym, "side": side,
                   "qty": order.quantity, "ref": order.paper_order_id,
                   "why": d["reason"] + " (queued next-open)",
                   "source": "runner:rsi2"}
            if side == "BUY":
                earn, checked = _next_earnings_checked(sym)
                row["next_earnings"], row["earnings_checked"] = earn, checked
                row["why"] += (f" · next earnings {earn}" if earn
                               else " · no earnings ≤21d" if checked
                               else " · earnings unchecked")
            action_log.append([row])

        paper_service.save_account(acct)
        rsi2_store.save(state, iso)
        try:
            from . import journal_ingest
            journal_ingest.sync_paper()
        except Exception:
            _log.exception("rsi2: journal sync failed")

        stale = settle_res["stale"]
        result = "error" if (stale or (errors and placed == 0 and not settle_res["settled"])) else "ok"
        summary = (f"RSI2: queued {exits} exit(s), {entries} entr(y/ies)"
                   + (f" · settled {len(settle_res['settled'])}" if settle_res["settled"] else "")
                   + (f" · {len(settle_res['rejected'])} rejected at open" if settle_res["rejected"] else "")
                   + (f" · ⚠ {len(stale)} stale queued order(s)" if stale else "")
                   + (f", {len(errors)} error(s)" if errors else ""))
        run_log.append([{"key": "rsi2", "ts": iso, "result": result, "summary": summary,
                         "placed": placed, "errors": errors}])
        return {"result": result, "placed": placed, "exits": exits, "entries": entries,
                "errors": errors, "summary": summary, "ran_at": iso}
