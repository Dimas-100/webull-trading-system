"""Pure, deterministic paper-trading engine. No network: every function operates on a
PaperAccount plus plain values (current prices, timestamps passed in by the caller),
exactly like strategy/backtest.py. Reuses ONLY the pure safety.validate_order/build_order;
never imports trading.place or safety.should_submit (enforced by tests/test_paper_safety.py)."""
from __future__ import annotations

import uuid

from .. import safety
from .schema import PaperAccount, PaperError, PaperOrder, PaperPosition

_EPS = 1e-9


def open_account(starting_cash: float, now_iso: str, account_id: str = "default") -> PaperAccount:
    if starting_cash <= 0:
        raise PaperError(f"starting_cash must be > 0, got {starting_cash}")
    return PaperAccount(account_id=account_id, starting_cash=starting_cash,
                        cash=starting_cash, created_at=now_iso, updated_at=now_iso)


def reserved_cash(acct: PaperAccount) -> float:
    """Cash committed to open BUY orders: LIMIT buys at qty*limit, and QUEUED (next_open)
    MARKET buys at qty*ref_price — several same-evening queued entries must not spend the
    same cash. Open immediate MARKET buys stay unreserved (they fill on the same request)."""
    total = 0.0
    for o in acct.open_orders:
        if o.side != "BUY":
            continue
        if o.order_type == "LIMIT":
            total += o.quantity * (o.limit_price or 0.0)
        elif o.fill_policy == "next_open":
            total += o.quantity * (o.ref_price or 0.0)
    return total


def buying_power(acct: PaperAccount) -> float:
    return acct.cash - reserved_cash(acct)


def _held(acct: PaperAccount, symbol: str) -> float:
    pos = acct.positions.get(symbol)
    return pos.quantity if pos else 0.0


def _committed_sell(acct: PaperAccount, symbol: str) -> float:
    return sum(o.quantity for o in acct.open_orders if o.side == "SELL" and o.symbol == symbol)


def place_order(acct: PaperAccount, *, symbol, side, order_type, quantity, limit_price,
                time_in_force, now_iso, today_et, last_price, thesis=None,
                strategy_id=None, trial_id=None, fill_policy="immediate") -> tuple[PaperAccount, PaperOrder]:
    """Validate (reusing safety) + paper checks (buying power / oversell), then append a
    pending order. Raises OrderValidationError/PaperError (both map to HTTP 400)."""
    built = safety.build_order(symbol=symbol, side=side, quantity=quantity,
                               order_type=order_type, limit_price=limit_price,
                               time_in_force=time_in_force)
    # Structural validation only (side/type/tif/qty/limit sanity). We deliberately do NOT pass
    # last_price: paper trading allows resting limit orders at any distance from the market
    # (e.g. a GTC buy far below to catch a dip), unlike the real ticket's 20%-from-last guard.
    safety.validate_order(built)
    sym = built["symbol"]
    qty = float(quantity)
    lp = float(limit_price) if (order_type == "LIMIT" and limit_price is not None) else None

    if side == "BUY":
        ref = lp if order_type == "LIMIT" else last_price
        if ref is None:
            raise PaperError(f"no market price available for {sym}")
        if qty * ref > buying_power(acct) + _EPS:
            raise PaperError(f"insufficient buying power: need {qty * ref:.2f}, "
                             f"have {buying_power(acct):.2f}")
    else:  # SELL
        avail = _held(acct, sym) - _committed_sell(acct, sym)
        if qty > avail + _EPS:
            raise PaperError(f"cannot sell {qty:g} {sym}: only {avail:g} available")

    order = PaperOrder(paper_order_id=uuid.uuid4().hex, symbol=sym, side=side,
                       order_type=order_type, quantity=qty, limit_price=lp,
                       time_in_force=time_in_force, status="pending",
                       created_at=now_iso, placed_et_date=today_et, thesis=thesis,
                       strategy_id=strategy_id, trial_id=trial_id, fill_policy=fill_policy,
                       ref_price=(last_price if (fill_policy == "next_open" and side == "BUY"
                                                 and order_type == "MARKET") else None))
    acct.open_orders.append(order)
    acct.updated_at = now_iso
    return acct, order


def _fill_price(order: PaperOrder, price: float) -> float | None:
    """The price an order fills at given the current last, or None if it should rest."""
    if order.order_type == "MARKET":
        return price
    if order.side == "BUY":
        return order.limit_price if price <= order.limit_price else None
    return order.limit_price if price >= order.limit_price else None


def _apply_buy(acct: PaperAccount, symbol: str, qty: float, price: float) -> None:
    pos = acct.positions.get(symbol)
    if pos is None or pos.quantity <= _EPS:
        acct.positions[symbol] = PaperPosition(symbol=symbol, quantity=qty, avg_cost=price)
    else:
        total = pos.quantity + qty
        pos.avg_cost = (pos.avg_cost * pos.quantity + price * qty) / total
        pos.quantity = total


def _apply_sell(acct: PaperAccount, symbol: str, qty: float, price: float) -> None:
    pos = acct.positions[symbol]
    acct.realized_pnl += (price - pos.avg_cost) * qty
    pos.quantity -= qty
    if pos.quantity <= _EPS:
        del acct.positions[symbol]


def _execute_fill(acct: PaperAccount, o: PaperOrder, fp: float) -> bool:
    """Apply one order's fill at price fp, re-checking BUY affordability / SELL held-quantity
    against the account AS OF NOW (earlier fills in the same pass are visible). On success:
    stamps status/fill_price, applies position+cash effects, appends to history, returns True.
    On a failed guard: stamps rejected, appends to history, returns False. Shared by
    evaluate + settle_next_open so the two paths' guards stay identical."""
    if o.side == "BUY":
        cost = o.quantity * fp
        if cost > acct.cash + _EPS:
            o.status = "rejected"
            acct.history.append(o)
            return False
        _apply_buy(acct, o.symbol, o.quantity, fp)
        acct.cash -= cost
    else:
        if o.quantity > _held(acct, o.symbol) + _EPS:
            o.status = "rejected"
            acct.history.append(o)
            return False
        _apply_sell(acct, o.symbol, o.quantity, fp)
        acct.cash += o.quantity * fp
    o.status = "filled"
    o.fill_price = fp
    acct.history.append(o)
    return True


def evaluate(acct: PaperAccount, prices: dict[str, float], now_iso: str,
             today_et: str) -> tuple[PaperAccount, list[PaperOrder]]:
    """Advance all open orders against current prices. Returns (account, newly-filled orders).
    Processes orders in placement order so a fill's cash/position effect is visible to later
    orders in the same pass (the conservative guard). Stamps updated_at."""
    fills: list[PaperOrder] = []
    still_open: list[PaperOrder] = []
    for o in acct.open_orders:
        if o.fill_policy == "next_open":
            still_open.append(o)   # settle_next_open's job — never fill or expire here
            continue
        if o.time_in_force == "DAY" and o.placed_et_date < today_et:
            o.status = "expired"
            acct.history.append(o)
            continue
        price = prices.get(o.symbol)
        if price is None:
            still_open.append(o)
            continue
        fp = _fill_price(o, price)
        if fp is None:
            still_open.append(o)
            continue
        if not _execute_fill(acct, o, fp):
            continue
        o.filled_at = now_iso
        fills.append(o)
    acct.open_orders = still_open
    acct.updated_at = now_iso
    return acct, fills


def settle_next_open(acct: PaperAccount, bars_by_symbol: dict, now_iso: str,
                     ) -> tuple[PaperAccount, list[PaperOrder], list[PaperOrder], list[PaperOrder]]:
    """Fill queued (fill_policy='next_open') orders at the open of the FIRST daily bar dated
    strictly after each order's placed_et_date — the price a real after-hours order would get,
    historically correct even when runs are missed (the fill comes from the bar, never 'now').
    bars_by_symbol: {SYM: [{"date": "YYYY-MM-DD", "open": float}, ...]} oldest-first (caller
    fetches; this stays pure). Returns (acct, fills, rejected, resting). BUY affordability and
    SELL held-quantity are re-checked at the actual open (mirrors evaluate's guards); a missing
    qualifying bar leaves the order resting for the next settle. Placement order, like evaluate."""
    fills: list[PaperOrder] = []
    rejected: list[PaperOrder] = []
    resting: list[PaperOrder] = []
    still_open: list[PaperOrder] = []
    for o in acct.open_orders:
        if o.fill_policy != "next_open":
            still_open.append(o)
            continue
        bar = next((b for b in bars_by_symbol.get(o.symbol, [])
                    if b.get("date") and b.get("open") is not None
                    and str(b["date"]) > o.placed_et_date), None)
        if bar is None:
            resting.append(o)
            still_open.append(o)
            continue
        fp = float(bar["open"])
        if not _execute_fill(acct, o, fp):
            rejected.append(o)
            continue
        o.fill_session = str(bar["date"])
        o.filled_at = now_iso
        fills.append(o)
    acct.open_orders = still_open
    acct.updated_at = now_iso
    return acct, fills, rejected, resting


def cancel_order(acct: PaperAccount, paper_order_id: str) -> PaperAccount:
    rest: list[PaperOrder] = []
    for o in acct.open_orders:
        if o.paper_order_id == paper_order_id:
            o.status = "cancelled"
            acct.history.append(o)
        else:
            rest.append(o)
    acct.open_orders = rest
    return acct


def trim_history(acct: PaperAccount, cap: int) -> PaperAccount:
    """Bound the persisted order history to the most recent `cap` entries so the JSON
    account file doesn't grow without limit over months of use."""
    if cap >= 0 and len(acct.history) > cap:
        acct.history = acct.history[-cap:]
    return acct


def account_view(acct: PaperAccount, prices: dict[str, float]) -> dict:
    """A JSON-ready snapshot of the account marked to the given prices. Pure.
    net_liquidation omits positions with no price — unmarked_positions discloses the count
    so the headline number is honest about its gap."""
    positions = []
    market_total = 0.0
    unmarked = 0
    for sym, pos in acct.positions.items():
        last = prices.get(sym)
        mv = last * pos.quantity if last is not None else None
        upnl = (last - pos.avg_cost) * pos.quantity if last is not None else None
        if mv is not None:
            market_total += mv
        else:
            unmarked += 1
        positions.append({"symbol": sym, "quantity": pos.quantity, "avg_cost": pos.avg_cost,
                          "last": last, "market_value": mv, "unrealized_pnl": upnl})
    return {
        "account_id": acct.account_id,
        "starting_cash": acct.starting_cash,
        "cash": acct.cash,
        "buying_power": buying_power(acct),
        "realized_pnl": acct.realized_pnl,
        "net_liquidation": acct.cash + market_total,
        "unmarked_positions": unmarked,   # positions excluded from net_liquidation (no mark)
        "positions": positions,
        "open_orders": [o.model_dump() for o in acct.open_orders],
        "history": [o.model_dump() for o in acct.history[-50:]],
        "updated_at": acct.updated_at,
    }
