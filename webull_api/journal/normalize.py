"""Adapters mapping each source's raw records into normalized Fill / PracticeDecision.
Pure given an injected context_fn. Network/disk live in the web ingest layer."""
from __future__ import annotations

from typing import Callable

from webull_api.analysis import support_resistance, technicals
from webull_api.journal.schema import Fill, MarketContext, OptionTrade, PracticeDecision
from webull_api.paper.options_schema import OptionsPaperAccount
from webull_api.paper.schema import PaperAccount
from webull_api.strategy.backtest import run_backtest
from webull_api.strategy.replay import ReplayState

ContextFn = Callable[[str, str], "MarketContext | None"]


def _firststr(o: dict, keys: list[str]) -> str:
    for k in keys:
        v = o.get(k)
        if v is not None and v != "":
            return str(v)
    return ""


def _firstnum(o: dict, keys: list[str]) -> float | None:
    for k in keys:
        v = o.get(k)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return None


def _flatten_orders(raw) -> list[dict]:
    """Expand Webull combo envelopes {orders:[leg...]} / {data:[...]} into a flat leg list."""
    if isinstance(raw, list):
        arr = raw
    elif isinstance(raw, dict) and isinstance(raw.get("data"), list):
        arr = raw["data"]
    else:
        arr = []
    out: list[dict] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("orders"), list):
            out.extend(s for s in item["orders"] if isinstance(s, dict))
        else:
            out.append(item)
    return out


def fills_from_order_history(raw, account_id: str, context_fn: ContextFn | None = None,
                             skip_ids: set[str] | None = None) -> list[Fill]:
    out: list[Fill] = []
    for o in _flatten_orders(raw):
        if _firststr(o, ["status"]).upper() != "FILLED":
            continue
        oid = _firststr(o, ["client_order_id", "clientOrderId", "order_id"])
        symbol = _firststr(o, ["symbol", "ticker"])
        side = _firststr(o, ["side"]).upper()
        if not oid or not symbol or side not in ("BUY", "SELL"):
            continue
        if skip_ids and oid in skip_ids:  # already journaled — skip before the context fetch
            continue
        qty = _firstnum(o, ["filled_quantity", "filledQuantity", "total_quantity", "quantity", "qty"])
        price = _firstnum(o, ["filled_price", "avg_filled_price", "filledPrice"])
        filled_at = _firststr(o, ["filled_time_at", "update_time_at", "filledAt"])
        if qty is None or price is None or not filled_at:
            continue
        otype = _firststr(o, ["order_type", "orderType"]).upper() or "MARKET"
        ctx = context_fn(symbol, filled_at) if context_fn else None
        out.append(Fill(id=oid, source="real", account_id=account_id, symbol=symbol, side=side,
                        quantity=qty, price=price, filled_at_iso=filled_at, order_type=otype,
                        context=ctx))
    return out


def _fmt_strike(strike: float) -> str:
    return str(int(strike)) if float(strike).is_integer() else str(strike)


def _legs_desc(legs) -> str:
    """Compact human label for an options unit, e.g. 'AAPL +300C / -310C 2026-07-17' (+ = long leg)."""
    if not legs:
        return ""
    parts = []
    for lg in legs:
        cp = "C" if lg.option_type == "CALL" else "P"
        sign = "+" if lg.side == "BUY" else "-"
        parts.append(f"{sign}{_fmt_strike(lg.strike)}{cp}")
    return f"{legs[0].underlying} {' / '.join(parts)} {legs[0].expiration}"


def option_trades_from_options_account(acct, skip_ids: set[str] | None = None) -> list[OptionTrade]:
    """Build closed options trades from a (possibly capped) options-paper account history. Reads only
    records the engine stamped with realized P&L — a filled CLOSE order or an 'expired' settlement.
    Pure; id = the close event's paper_order_id (dedup key)."""
    account = OptionsPaperAccount.model_validate(acct)
    out: list[OptionTrade] = []
    for o in account.history:
        if o.realized_pnl is None or o.closed_qty is None or o.open_net_price is None:
            continue  # OPEN / pending / cancelled / pre-feature record — no P&L stamp
        is_close = o.status == "filled" and o.intent == "CLOSE"
        is_expired = o.status == "expired"
        if not (is_close or is_expired):
            continue
        if skip_ids and o.paper_order_id in skip_ids:
            continue
        if not o.filled_at or not o.unit_opened_at or not o.legs:
            continue
        risk = o.risk_basis or 0.0
        out.append(OptionTrade(
            id=o.paper_order_id, underlying=o.legs[0].underlying, strategy=o.strategy,
            legs_desc=_legs_desc(o.legs), quantity=o.closed_qty,
            open_net_price=o.open_net_price,
            close_value=o.close_value if o.close_value is not None else 0.0,
            opened_at_iso=o.unit_opened_at, closed_at_iso=o.filled_at,
            pnl=o.realized_pnl, win=o.realized_pnl > 0,
            return_pct=(o.realized_pnl / risk * 100.0) if risk else 0.0,
            reason="expired" if is_expired else "closed",
            settle_basis=o.settle_basis))
    return out


def fills_from_paper_account(acct_dict: dict, context_fn: ContextFn | None = None,
                             skip_ids: set[str] | None = None) -> list[Fill]:
    acct = PaperAccount.model_validate(acct_dict)
    out: list[Fill] = []
    for o in acct.history:
        if o.status != "filled" or o.fill_price is None or not o.filled_at:
            continue
        if skip_ids and o.paper_order_id in skip_ids:  # already journaled — skip the context fetch
            continue
        ctx = context_fn(o.symbol, o.filled_at) if context_fn else None
        out.append(Fill(id=o.paper_order_id, source="paper", account_id=acct.account_id,
                        symbol=o.symbol, side=o.side, quantity=o.quantity, price=o.fill_price,
                        filled_at_iso=o.filled_at, order_type=o.order_type, context=ctx,
                        thesis=o.thesis, strategy_id=o.strategy_id, trial_id=o.trial_id))
    return out


def _context_from_window(bars: list[dict], i: int) -> MarketContext | None:
    sub = bars[: i + 1]
    if not sub:
        return None
    try:
        t = technicals(sub)
        sr = support_resistance(sub)
    except Exception:
        return None
    return MarketContext(as_of_iso=sub[-1].get("time"), trend=t["trend"], rsi14=t["rsi14"],
                         sma20=t["sma20"], sma50=t["sma50"], pct_change_5=t["pct_change_5"],
                         support=sr["support"], resistance=sr["resistance"])


def _mfe_mae(bars: list[dict], entry_time: str, exit_time: str,
             entry_price: float) -> tuple[float | None, float | None]:
    """Pure MFE/MAE (% of entry, signed) over the hold window [entry_time, exit_time] inclusive.
    Matches bars by b["time"] string equality for endpoints, slices inclusive. No network/imports."""
    if not entry_price or entry_price <= 0:
        return None, None
    try:
        # Find inclusive start and end indices by time string match
        start_idx = next((i for i, b in enumerate(bars) if b.get("time") == entry_time), None)
        end_idx = next((i for i, b in enumerate(bars) if b.get("time") == exit_time), None)
        if start_idx is None or end_idx is None or end_idx < start_idx:
            return None, None
        window = bars[start_idx:end_idx + 1]
        if not window:
            return None, None
        highs = [b.get("high") for b in window]
        lows = [b.get("low") for b in window]
        if any(h is None for h in highs) or any(lo is None for lo in lows):
            return None, None
        highest = max(highs)
        lowest = min(lows)
        P = entry_price
        mfe = (highest - P) / P * 100
        mae = (lowest - P) / P * 100
        return mfe, mae
    except Exception:
        return None, None


def decisions_from_session(session_dict: dict) -> list[PracticeDecision]:
    state = ReplayState.model_validate({k: v for k, v in session_dict.items() if k != "id"})
    if state.status != "finished":
        return []
    session_id = str(session_dict.get("id", ""))
    bars = state.window_bars
    try:
        result = run_backtest(state.strategy, bars)
        trade_by_entry: dict[str, object] = {}
        for tr in result.trades:
            trade_by_entry.setdefault(tr.entry_time, tr)
    except Exception:
        trade_by_entry = {}

    out: list[PracticeDecision] = []
    for d in state.decisions:
        i = d.bar_index
        entry_time = bars[i + 1]["time"] if 0 <= i + 1 < len(bars) else None
        bt = trade_by_entry.get(entry_time) if entry_time else None
        decided_at = bars[i].get("time", "") if 0 <= i < len(bars) else ""
        close_px = bars[i].get("close") if 0 <= i < len(bars) else None
        if bt is not None:
            mfe, mae = _mfe_mae(bars, bt.entry_time, bt.exit_time, bt.entry_price)
        else:
            mfe, mae = None, None
        out.append(PracticeDecision(
            id=f"{session_id}:{i}",
            session_id=session_id,
            strategy_name=state.strategy.name,
            symbol=state.symbol or state.strategy.symbol,
            decided_at_iso=decided_at,
            choice=d.choice,
            signal_why=d.why,
            entry_price=(bt.entry_price if bt else close_px),
            signal_pnl=(bt.pnl if bt else None),
            signal_win=((bt.pnl > 0) if bt else None),
            context=_context_from_window(bars, i),
            thesis=d.thesis,
            mfe=mfe,
            mae=mae))
    return out
