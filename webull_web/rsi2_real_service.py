"""REAL-book RSI2 runner — impure shell that QUEUES executable decisions and NEVER places.

The autopilot's DECISIONS stage is the only thing that turns a queued row into an order, so this
module inherits gate.authorize, the caps, cooling, replay refusal and cancel-then-sell without
adding a second order path. Deliberately separate from rsi2_service, whose contract is "100%
paper, no real-order path" — a paper runner that queued real rows would break that in substance.

OFF unless WEBULL_RSI2_REAL_ENABLED is truthy. Always writes exactly one run-log row (watchdog
EXPECTED_KEYS alarms on a missing key).
Spec: docs/superpowers/specs/2026-08-15-real-book-rsi2-path-design.md"""
from __future__ import annotations

import logging
import math
import os
from datetime import date, timedelta

from webull_api import decisions_exec, run_log
from webull_api.strategy import rsi2 as rsi2_strategy

from . import paper_service, runner_util

_log = logging.getLogger(__name__)
_KEY = "rsi2_real"
_TRUTHY = {"1", "true", "yes", "on"}


def enabled() -> bool:
    return os.environ.get("WEBULL_RSI2_REAL_ENABLED", "").strip().lower() in _TRUTHY


def real_account_id(list_fn=None) -> str | None:
    """The INDIVIDUAL_CASH account. Never accounts[0] — that is the Crypto account."""
    if list_fn is None:
        from webull_api import portfolio
        list_fn = portfolio.list_accounts
    rows = list_fn() or []
    cash = next((a for a in rows if isinstance(a, dict)
                 and a.get("account_class") == "INDIVIDUAL_CASH"), None)
    return str(cash.get("account_id")) if cash else None


def settled_cash(balance: dict) -> float | None:
    """SETTLED cash only. None when the field is absent — never fall back to buying_power or
    cash_balance: sizing from unsettled funds is what creates Good-Faith Violations."""
    for asset in (balance or {}).get("account_currency_assets") or []:
        if not isinstance(asset, dict):
            continue
        raw = asset.get("settled_cash")
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None
    return None


def net_liq(balance: dict) -> float | None:
    """Net liquidation value: the account total first, else the first currency row's. None when
    absent or unparseable — NEVER buying_power or cash: the divisor sizes a slot from what the
    account is worth, and a missing figure must fail closed upstream, not widen."""
    raw = (balance or {}).get("total_net_liquidation_value")
    if raw is None:
        for asset in (balance or {}).get("account_currency_assets") or []:
            if isinstance(asset, dict) and asset.get("net_liquidation_value") is not None:
                raw = asset.get("net_liquidation_value")
                break
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _next_trading_day(today_iso: str) -> str:
    """The next trading weekday after `today_iso`. Pure.

    An entry row must NOT expire tonight: the suite queues at ~17:30, the autopilot's 120-minute
    BUY cooling vetoes the 17:45 run, and a row stamped `expires == today` is then already expired
    at every later run — the entry could never place (final-review C1). Holidays are deliberately
    not modeled: a row landing on one simply expires unfilled and that evening's run re-queues the
    signal, which is the same fail-safe direction as an expiry that is too short.
    """
    d = date.fromisoformat(str(today_iso)[:10]) + timedelta(days=1)
    while d.weekday() >= 5:            # 5 = Sat, 6 = Sun
        d += timedelta(days=1)
    return d.isoformat()


def _padded_prices(price_by_symbol: dict) -> dict:
    """Last close -> a 1% marketable BUY limit, rounded UP to the cent so the buffer survives.

    Both the queued limit AND the share sizing use this number: a cash account rejects a fill it
    cannot fund, so sizing at the last close while queueing a padded limit could overspend settled
    cash by up to the pad. Ceil-to-cent means the worst-case fill cost is never underestimated.
    """
    out: dict = {}
    for sym, px in (price_by_symbol or {}).items():
        try:
            p = float(px)
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        out[sym] = math.ceil(p * 1.01 * 100) / 100
    return out


def _cap() -> float:
    """The autopilot's per-order cap, parsed by the gate's OWN config so the runner and the gate can
    never disagree about it. Reads os.environ like every other env read in this module: the suite
    entrypoint (runner_cli.load_repo_env) and client.get_settings() have loaded `.env` long before
    run() gets here. Deliberately no load_dotenv of its own — that would leak the real `.env` into
    the test process."""
    from webull_api.autopilot.config import AutopilotConfig
    return float(AutopilotConfig.from_env().max_notional)


def _budget_clause(*, basis: str, dollars: float | None, net_liq_value: float | None,
                   divisor: float | None, cap: float | None) -> str | None:
    """The evening note's budget words (spec §4). None when the divisor is unset — the summary must
    then read exactly as it did before the divisor existed."""
    if basis == "unset" or dollars is None or net_liq_value is None or not divisor:
        return None
    slot = net_liq_value / divisor
    div = f"{divisor:g}"
    if basis == "divisor":
        return f"budget ${dollars:,.2f} (net liq ${net_liq_value:,.2f} ÷ {div})"
    if basis == "cap":
        return (f"budget ${dollars:,.2f} (cap binds: net liq ${net_liq_value:,.2f} ÷ {div} = "
                f"${slot:,.2f} — raise WEBULL_AUTOPILOT_MAX_NOTIONAL)")
    return (f"budget ${dollars:,.2f} (WEBULL_RSI2_REAL_DOLLARS binds: net liq ${net_liq_value:,.2f} "
            f"÷ {div} = ${slot:,.2f} — delete it to float)")


def _save(state: dict, iso: str, errors: list) -> bool:
    """Persist the ledger. Never lets an I/O failure escape run(): the runner's contract is that
    exactly one run-log row is written every night, and the watchdog alarms on a missing key."""
    from . import rsi2_real_store
    try:
        rsi2_real_store.save(state, iso)
        return True
    except Exception as e:
        errors.append({"error": f"ledger save failed: {str(e)[:80]}"})
        return False


def _report(result: str, summary: str, iso: str, errors=None, **extra) -> dict:
    errors = errors or []
    run_log.append([{"key": _KEY, "ts": iso, "result": result, "summary": summary,
                     "placed": 0, "errors": errors}])
    return {"result": result, "errors": errors, "summary": summary, "ran_at": iso, **extra}


def run(force: bool = False, *, list_fn=None, positions_fn=None, balance_fn=None,
        rsi_fn=None, price_fn=None, fills_fn=None, append_fn=None) -> dict:
    from . import rsi2_real_store

    iso, today = paper_service.now_et()
    if not enabled():
        return _report("no_op", "RSI2-real: disabled (WEBULL_RSI2_REAL_ENABLED unset)", iso)

    with runner_util.filelock(_KEY, iso) as got:
        if not got:
            return _report("no_op", "RSI2-real: another run in progress", iso)
        if runner_util.already_ran_today(_KEY, today, force):
            return _report("no_op", "RSI2-real: already ran today", iso)

        errors: list = []
        # Every outside read is wrapped: an exception escaping run() would skip the run-log row
        # this runner is contracted to write every night, and the watchdog pages on the gap (I3).
        try:
            acct = real_account_id(list_fn)
        except Exception as e:
            return _report("error", f"RSI2-real: account list failed ({type(e).__name__})", iso,
                           errors=[{"error": str(e)[:120]}], entries=[], unaffordable=[])
        if not acct:
            return _report("error", "RSI2-real: no INDIVIDUAL_CASH account", iso,
                           errors=[{"error": "no INDIVIDUAL_CASH account"}])

        if positions_fn is None:
            from webull_api import portfolio
            positions_fn = portfolio.get_positions
        try:
            positions = runner_util.retry_throttled(lambda: positions_fn(acct)) or []
        except Exception as e:
            return _report("error", f"RSI2-real: positions unreadable ({type(e).__name__})", iso,
                           errors=[{"error": str(e)[:120]}])

        state = rsi2_real_store.load()
        # Adopt BEFORE reconcile: a decision queued yesterday and placed by the autopilot shows
        # up as a broker position today, and it must become a lot before reconcile prunes and
        # before entries count slots — otherwise owned_lots stays empty forever, max_lots never
        # fills, and the runner re-queues the same entry every night.
        state, adopted = rsi2_real_store.adopt_filled(state, positions, today)
        state, dropped = rsi2_real_store.reconcile(state, positions, today)
        notes = adopted + dropped

        # ---- retire standing exit rows whose lot is gone (C3). Cancellation is BY RECORDED ID
        # ONLY: the same queue carries the owner's own hand-queued rsi2_above rows protecting
        # positions this ledger does not own, and a symbol/kind pattern cancel would kill them.
        for sym, decision_id in rsi2_real_store.orphan_exit_rows(state):
            try:
                decisions_exec.mark(decision_id, "cancelled", "rsi2-real: lot no longer held")
            except Exception as e:
                errors.append({"symbol": sym, "error": str(e)[:120]})
                continue
            state = rsi2_real_store.drop_exit_row(state, symbol=sym)
            notes.append(f"cancelled orphaned exit row {sym}")

        # ---- the decisions queue, read ONCE (also reused by the exit-row dedup below)
        try:
            existing, _ = decisions_exec.load()
        except Exception as e:
            existing = []
            errors.append({"error": f"decisions unreadable: {str(e)[:80]}"})
        status_by_id = {r.get("id"): r.get("status") for r in existing if r.get("id")}
        state, pruned = rsi2_real_store.prune_pendings(state, status_by_id, today)
        notes += pruned

        if not _save(state, iso, errors):
            return _report("error", "RSI2-real: ledger unwritable — queued nothing", iso,
                           errors=errors, entries=[], unaffordable=[])

        # ---- attribution (2026-09-18): one action row per adopted lot, keyed by its journal
        # fill, so the entry reads as the system's (not a manual fill), the
        # evening note carries its why, and the round trip buckets as rsi2-real
        # (a hook for a future real-book read — the weekly scorecard is paper-only). Read +
        # append only; a failure is recorded, never fatal — the queue work below must happen.
        attributed: list[str] = []
        try:
            from . import rsi2_real_attribution
            attributed = rsi2_real_attribution.record(state, account_id=acct, fills_fn=fills_fn,
                                                      append_fn=append_fn)
        except Exception as e:
            errors.append({"error": f"attribution failed: {str(e)[:100]}"})

        # ---- config
        try:
            max_lots = int(os.environ.get("WEBULL_RSI2_REAL_MAX_LOTS", "1"))
        except ValueError:
            max_lots = 1
        raw_dollars = os.environ.get("WEBULL_RSI2_REAL_DOLLARS", "").strip()
        try:
            # UNSET means 'spend whatever settled cash allows'. A SET-but-garbled value must NOT
            # fall back to that: a typo would silently widen the per-signal budget to everything.
            dollars = float(raw_dollars) if raw_dollars else None
        except ValueError:
            return _report("error",
                           "RSI2-real: WEBULL_RSI2_REAL_DOLLARS unparseable — queued nothing",
                           iso, errors=errors + [{"error": f"WEBULL_RSI2_REAL_DOLLARS={raw_dollars!r}"}],
                           entries=[], unaffordable=[])
        raw_divisor = os.environ.get("WEBULL_RSI2_REAL_SLOT_DIVISOR", "").strip()
        try:
            # Spec 2026-09-23: set => budget = min(net liq / divisor, cap, DOLLARS). Same M7 rule:
            # a garbled value is an error, never 'spend everything'.
            divisor = float(raw_divisor) if raw_divisor else None
        except ValueError:
            return _report("error",
                           "RSI2-real: WEBULL_RSI2_REAL_SLOT_DIVISOR unparseable — queued nothing",
                           iso, errors=errors + [{"error": f"WEBULL_RSI2_REAL_SLOT_DIVISOR={raw_divisor!r}"}],
                           entries=[], unaffordable=[])

        # ---- settled cash (fail closed: no settled figure -> queue nothing)
        if balance_fn is None:
            from webull_api import portfolio
            balance_fn = portfolio.get_balance
        try:
            balance = runner_util.retry_throttled(lambda: balance_fn(acct))
        except Exception as e:
            return _report("error", f"RSI2-real: balance unreadable ({type(e).__name__})", iso,
                           errors=[{"error": str(e)[:120]}], entries=[], unaffordable=[])
        cash = settled_cash(balance)
        if cash is None:
            return _report("error", "RSI2-real: settled cash unreadable — queued nothing", iso,
                           errors=[{"error": "settled_cash missing"}], entries=[], unaffordable=[])

        # ---- per-signal budget (spec 2026-09-23 §2): with the divisor set, a slot is net liq /
        # divisor clipped under the autopilot's own cap so the gate can never deny a queued row;
        # unset, DOLLARS governs exactly as before. Both bad-read cases fail CLOSED.
        nl = net_liq(balance) if divisor is not None else None
        cap = _cap() if divisor is not None else None
        dollars, basis = rsi2_strategy.real_budget(net_liq=nl, divisor=divisor, dollars=dollars, cap=cap)
        if dollars is None and basis != "unset":
            msg = ("RSI2-real: net liq unreadable with WEBULL_RSI2_REAL_SLOT_DIVISOR set — queued nothing"
                   if basis == "net_liq"
                   else "RSI2-real: WEBULL_RSI2_REAL_SLOT_DIVISOR must be > 0 — queued nothing")
            return _report("error", msg, iso, errors=errors + [{"error": f"budget basis {basis}"}],
                           entries=[], unaffordable=[])
        cfg = rsi2_strategy.real_config(dollars=dollars, max_lots=max_lots)
        clause = _budget_clause(basis=basis, dollars=dollars, net_liq_value=nl, divisor=divisor, cap=cap)

        # ---- signals
        # An outstanding pending consumes a slot and its symbol, exactly like an owned lot:
        # the autopilot may place it at the very next run, and queueing a second entry against
        # the same slot would double-spend cash the account does not have.
        owned = state.get("owned_lots", [])
        claimed = list(owned) + [{"symbol": p["symbol"], "shares": p.get("shares", 0)}
                                 for p in state.get("pending_orders", [])]
        owned_syms = [l["symbol"] for l in claimed]
        symbols = list(dict.fromkeys(list(cfg.universe) + owned_syms))
        if rsi_fn is None:
            from . import rsi2_service
            rsi_fn = rsi2_service._fresh_rsi
        try:
            rsi_by_symbol = rsi_fn(today, symbols, errors) or {}
        except Exception as e:
            return _report("error", f"RSI2-real: RSI read failed ({type(e).__name__})", iso,
                           errors=errors + [{"error": str(e)[:120]}], entries=[], unaffordable=[])
        if price_fn is None:
            price_fn = paper_service.last_prices
        try:
            price_by_symbol = price_fn(symbols) or {}
        except Exception as e:
            # MarketDataNotEntitledError is deliberately NOT re-raised: the type name lands in the
            # summary so the manager's note surfaces the entitlement, and the run-log row survives.
            return _report("error", f"RSI2-real: price read failed ({type(e).__name__})", iso,
                           errors=errors + [{"error": str(e)[:120]}], entries=[], unaffordable=[])

        # Size AND queue at the padded limit — never at the raw close (see _padded_prices).
        limits = _padded_prices(price_by_symbol)
        entries = rsi2_strategy.decide_entries_cash(
            owned_lots=claimed, settled_cash=cash, rsi_by_symbol=rsi_by_symbol,
            price_by_symbol=limits, cfg=cfg)
        skipped = rsi2_strategy.unaffordable(
            owned_lots=claimed, settled_cash=cash, rsi_by_symbol=rsi_by_symbol,
            price_by_symbol=limits, cfg=cfg)

        expires = _next_trading_day(today)
        queued: list[str] = []
        for d in entries:
            qty = d["quantity"]
            qty_str = str(int(qty)) if float(qty).is_integer() else str(qty)
            limit = limits[d["symbol"]]
            try:
                stored = decisions_exec.append({
                    "asset": "EQUITY", "symbol": d["symbol"], "side": "BUY", "qty": qty_str,
                    # LIMIT, not MARKET: the gate prices a row to enforce its notional cap and a
                    # MARKET row has no price, so it is denied at the cap layer forever (C2).
                    "order_type": "LIMIT", "limit_price": f"{limit:.2f}",
                    "trigger": {"kind": "immediate"},
                    "expires": expires,
                    "note": f"rsi2-real entry: {d['reason']} · LIMIT {limit:.2f}",
                })
                # Record the INTENT so the next run can adopt it once the broker shows a fill.
                # Never a lot yet — the autopilot may still refuse it at the gate.
                state = rsi2_real_store.record_pending(
                    state, decision_id=stored["id"], symbol=d["symbol"],
                    shares=d["quantity"], queued_date=today)
                queued.append(d["symbol"])
            except Exception as e:
                errors.append({"symbol": d["symbol"], "error": str(e)[:120]})
        if queued and not _save(state, iso, errors):
            return _report("error",
                           f"RSI2-real: {len(queued)} entr(y/ies) queued but the ledger did not "
                           "save — reconcile by hand before the next run", iso, errors=errors,
                           entries=queued, exits_queued=[], unaffordable=skipped, reconciled=notes,
                           budget=dollars, budget_basis=basis, net_liq=nl)

        # ---- standing exits: exactly one live rsi2_above row per held lot.
        # A standing row beats emitting a SELL when the band is already crossed: the autopilot
        # re-reads RSI(2) at placement time, so the decision acts on fresh data even if this
        # runner does not run that day.
        exits_queued: list[str] = []
        live_exit_syms = {
            str(r.get("symbol") or "").strip().upper()
            for r in existing
            if r.get("side") == "SELL" and r.get("status") == "queued"
            and (r.get("trigger") or {}).get("kind") == "rsi2_above"
        }
        for lot in state.get("owned_lots", []):
            sym = str(lot.get("symbol") or "").strip().upper()
            if sym in live_exit_syms:
                # Deliberately does NOT record the observed row's id: the live row may be the
                # owner's own hand-queued protection, and an id in the map is an id this runner
                # may cancel. Only rows it appended itself are ever recorded.
                continue
            try:
                stored = decisions_exec.append({
                    "asset": "EQUITY", "symbol": sym, "side": "SELL", "qty": "ALL",
                    # The threshold travels WITH the row so the executor and this runner can never
                    # disagree about the band (decision_triggers reads trigger["threshold"]).
                    "order_type": "MARKET",
                    "trigger": {"kind": "rsi2_above", "threshold": cfg.exit_above},
                    "expires": "2026-12-31",
                    "note": f"rsi2-real standing exit: RSI(2) > {cfg.exit_above:g} band",
                })
                state = rsi2_real_store.record_exit_row(
                    state, symbol=sym, decision_id=stored["id"])
                exits_queued.append(sym)
                live_exit_syms.add(sym)
            except Exception as e:
                errors.append({"symbol": sym, "error": str(e)[:120]})
        ledger_ok = _save(state, iso, errors) if exits_queued else True

        bits = [f"RSI2-real: {len(queued)} entr(y/ies) queued"]
        if clause:
            bits.append(clause)
        if exits_queued:
            bits.append(f"{len(exits_queued)} exit row(s) armed")
        if skipped:
            bits.append(f"{len(skipped)} unaffordable")
        if notes:
            bits.append(f"reconciled {len(notes)}")
        written = [n for n in attributed if n.startswith("attributed ")]
        if written:
            bits.append(f"{len(written)} attributed")
        if not ledger_ok:
            bits.append("ledger save FAILED — exit-row ids unrecorded")
        return _report("ok" if ledger_ok else "error", " · ".join(bits), iso, errors=errors,
                       entries=queued, exits_queued=exits_queued,
                       unaffordable=skipped, reconciled=notes, attributed=attributed,
                       budget=dollars, budget_basis=basis, net_liq=nl)
