# webull_web/exit_plans_service.py
"""What will make you sell — per-holding exit plans, resolved from the ACTUAL rule
sources (read-only): the RSI2 ledger, the proven-runner ownership + Lab strategy records,
the paper-EOD/options-EOD backstop configs, and SELL stop orders resting at the broker.
Lookup contract for the UI: map[symbol] ?? map["*"]. Unknown -> absent, never invented."""
from __future__ import annotations

import logging
from datetime import date

from webull_api import exits as exits_mod
from webull_api.strategy.rsi2 import DEFAULT_CONFIG as RSI2_CFG

from . import paper_service, runner_util

_log = logging.getLogger(__name__)


def _days_since(entry_date: str | None, today: str) -> int | None:
    try:
        return (date.fromisoformat(str(today)) - date.fromisoformat(str(entry_date)[:10])).days + 1
    except (TypeError, ValueError):
        return None


def _rsi2_plans(today: str) -> dict[str, str]:
    from . import rsi2_store
    plans: dict[str, str] = {}
    for lot in rsi2_store.load().get("owned_lots") or []:
        sym = lot.get("symbol")
        if not sym:
            continue
        day = _days_since(lot.get("entry_date"), today)
        held = f" · day {day} of hold" if day is not None else ""
        plans[str(sym)] = f"RSI2: exit when RSI(2) ≥ {RSI2_CFG.exit_above:g}{held}"
    return plans


def _strategy_exit_text(name: str, strategy: dict) -> str:
    bits = []
    if strategy.get("stop_loss_pct"):
        bits.append(f"stop −{strategy['stop_loss_pct']:g}%")
    if strategy.get("take_profit_pct"):
        bits.append(f"target +{strategy['take_profit_pct']:g}%")
    if strategy.get("exit") is not None:
        bits.append("exit signal")
    return f"proven[{name}]: " + " · ".join(bits or ["signal-based"])


def _proven_plans() -> dict[str, str]:
    from . import proven_store
    owners = proven_store.load()
    if not owners:
        return {}
    # Obtain proven records via lab_service.proven_view(). It returns list[dict] (each built
    # via PaperProvenRecord.model_dump()), so records here are plain dicts — NOT model
    # instances — and must be read with .get(), not getattr(). This is the one adaptation
    # from the brief's sketch (which used getattr() as if records were still pydantic models).
    from . import lab_service
    records = lab_service.proven_view()
    by_trial: dict[str, dict] = {}
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        gate_b = rec.get("gate_b") or {}
        tid = gate_b.get("trial_id") if isinstance(gate_b, dict) else None
        if tid:
            by_trial[str(tid)] = rec
    plans: dict[str, str] = {}
    for sym, tid in owners.items():
        rec = by_trial.get(str(tid))
        if rec is None:
            continue
        strategy = rec.get("strategy")
        if not isinstance(strategy, dict):
            continue
        plans[str(sym)] = _strategy_exit_text(strategy.get("name") or str(tid), strategy)
    return plans


def _stops_from_rows(rows) -> dict[str, str]:
    """Tolerant parse of broker open-order rows -> {SYMBOL: stop_price}. SELL + STOP-typed
    orders only. Unknown shapes are skipped, never guessed."""
    out: dict[str, str] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        side = str(r.get("side") or "").upper()
        otype = str(r.get("order_type") or r.get("orderType") or "").upper()
        if side != "SELL" or "STOP" not in otype:
            continue
        sym = r.get("symbol")
        if not sym and isinstance(r.get("ticker"), dict):
            sym = r["ticker"].get("symbol")
        price = r.get("stop_price") or r.get("stopPrice") or r.get("aux_price")
        if sym and price is not None:
            out[str(sym)] = str(price)
    return out


def _order_rows(payload) -> list:
    """Broker open-orders payloads vary: a bare list, or nested under data/items/orders.

    Webull's live shape is a list of COMBO WRAPPERS carrying the real order(s) under `orders`:
    `[{"combo_type": "NORMAL", "combo_order_id": ..., "orders": [{...}]}]`. Returning that list
    unchanged fed _stops_from_rows the wrappers, which have no side/order_type — so every real
    stop was skipped and resting_stops() returned {} unconditionally (a $28.00 GTC stop on AAPL
    sat unreported for two days, 2026-08-13..15). Flatten one level of that envelope.
    """
    rows: list = []
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        for k in ("data", "items", "orders"):
            v = payload.get(k)
            if isinstance(v, list):
                rows = v
                break
    out: list = []
    for r in rows:
        if isinstance(r, dict) and isinstance(r.get("orders"), list):
            out.extend(o for o in r["orders"] if isinstance(o, dict))
        else:
            out.append(r)
    return out


def resting_stops() -> dict[str, str]:
    """SELL stop orders resting at the broker, across live accounts. RAISES on broker
    errors — callers decide how to degrade (build() drops per-symbol lines; the note
    composer says 'unavailable')."""
    from webull_api import portfolio, trading
    out: dict[str, str] = {}
    for a in runner_util.retry_throttled(portfolio.list_accounts) or []:
        if not isinstance(a, dict):
            continue
        aid = a.get("account_id") or a.get("id")
        if not aid:
            continue
        rows = runner_util.retry_throttled(lambda: trading.get_open_orders(str(aid)))
        out.update(_stops_from_rows(_order_rows(rows)))
    return out


def build() -> dict:
    _, today = paper_service.now_et()
    cfg = exits_mod.DEFAULT_EXIT_CONFIG
    eq_backstop = (f"backstop: stop −{cfg.stop_loss_pct:g}% · target +{cfg.take_profit_pct:g}%"
                   f" · 20-day trend-break (evening check)")
    paper_equity: dict[str, str] = {"*": eq_backstop}
    try:
        paper_equity.update(_proven_plans())
    except Exception:
        _log.exception("exit-plans: proven ledger unavailable")
    try:
        paper_equity.update(_rsi2_plans(today))  # rsi2 wins over proven if both claim a symbol
    except Exception:
        _log.exception("exit-plans: rsi2 ledger unavailable")

    paper_options = {"*": "close at +50% of debit · −50% stop · ≤7 DTE time-stop (evening check)"}

    real: dict[str, str] = {"*": f"−{cfg.stop_loss_pct:g}% backstop (reviewed at the evening check)"}
    real_stops: dict[str, float] = {}      # the sentence's structured twin: {SYMBOL: stop price}
    try:
        for sym, price in resting_stops().items():
            real[sym] = f"stop ${price} resting (GTC)"
            try:
                real_stops[sym] = float(price)
            except (TypeError, ValueError):
                continue                   # the sentence still shows what the broker said
    except Exception:
        _log.exception("exit-plans: broker open orders unavailable")

    return {"paper_equity": paper_equity, "paper_options": paper_options, "real": real,
            "real_stops": real_stops}
