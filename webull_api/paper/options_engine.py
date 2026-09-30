"""Pure, deterministic options paper-trading engine. No network: operates on an
OptionsPaperAccount plus plain price dicts. Imports only the pure safety + options_chain
helpers; NEVER imports trading.place / safety.should_submit / place_option
(enforced by tests/test_paper_options_safety.py)."""
from __future__ import annotations

import uuid

from .. import options_chain, safety
from .options_schema import (
    OptionLeg,
    OptionPaperOrder,
    OptionPositionUnit,
    OptionsPaperAccount,
    OptionsPaperError,
)

_MULT = 100.0
_EPS = 1e-9  # floating-point tolerance for buying-power comparison


def open_account(
    starting_cash: float, now_iso: str, account_id: str = "options"
) -> OptionsPaperAccount:
    """Create a fresh OptionsPaperAccount with the given starting cash."""
    if starting_cash <= 0:
        raise OptionsPaperError(f"starting_cash must be > 0, got {starting_cash}")
    return OptionsPaperAccount(
        account_id=account_id,
        starting_cash=starting_cash,
        cash=starting_cash,
        created_at=now_iso,
        updated_at=now_iso,
    )


def leg_objs(legs: list[dict]) -> list[OptionLeg]:
    """Build OptionLeg objects from [{occ, side}] by parsing the OCC symbol.

    Each dict must have 'occ' (an OCC contract symbol) and 'side' ("BUY" or "SELL").
    Raises OptionsPaperError on an invalid OCC symbol.
    """
    out: list[OptionLeg] = []
    for lg in legs:
        occ = str(lg["occ"]).strip().upper()
        try:
            info = options_chain.parse_occ(occ)
        except ValueError:
            raise OptionsPaperError(f"invalid option symbol {occ!r}")
        out.append(
            OptionLeg(
                occ=occ,
                underlying=info["root"],
                option_type="CALL" if info["kind"] == "C" else "PUT",
                strike=float(info["strike"]),
                expiration=info["exp"].isoformat(),
                side=lg["side"],
            )
        )
    return out


def net_price(
    legs: list[OptionLeg], marks: dict, *, fill: bool
) -> float | None:
    """Compute per-contract net price for a multi-leg strategy.

    When fill=True:  buy legs fill at ask, sell legs fill at bid (worst-case fill).
    When fill=False: all legs priced at mid (mark-to-market).

    Returns None if any leg's required price is missing from marks.

    Convention:  net > 0 = debit (cash out), net < 0 = credit (cash in).
    """
    total = 0.0
    for leg in legs:
        m = marks.get(leg.occ)
        if not m:
            return None
        if fill:
            px = m.get("ask") if leg.side == "BUY" else m.get("bid")
        else:
            px = m.get("mid")
        if px is None:
            return None
        # BUY legs add to net cost; SELL legs reduce it (credit received)
        if leg.side == "BUY":
            total += float(px)
        else:
            total -= float(px)
    return total


def spread_width(legs: list[OptionLeg]) -> float:
    """Strike width of a 2-leg vertical spread; 0 for singles or 3+ legs.

    Only 2-leg strategies (verticals) have a defined width for margin purposes.
    3+ leg combos are not yet supported; returning 0 is safe (no width credit).
    """
    if len(legs) != 2:
        return 0.0
    return abs(legs[0].strike - legs[1].strike)


def risk_capital(net: float, width: float, qty: int) -> float:
    """Buying power a position or order ties up.

    Debit positions/orders (net >= 0):
        risk = net * 100 * qty   (premium paid is the max loss)

    Credit spreads (net < 0, width > 0):
        risk = (width - abs(net)) * 100 * qty   (max loss = width minus credit received)

    Naked short (net < 0, width = 0):
        This should be blocked upstream; here we return 0 as a safe fallback
        (the engine doesn't allow naked shorts for paper trading).
    """
    if net >= 0:
        return net * _MULT * qty
    return max(0.0, (width - abs(net)) * _MULT * qty)


def _open_reservation(o: OptionPaperOrder) -> float:
    """Risk capital reserved by a pending OPEN order.

    CLOSE orders reserve nothing (they release existing collateral, handled elsewhere).
    MARKET OPEN orders reserve 0 here — they are expected to fill immediately on the
    same evaluation pass, not sit resting.
    LIMIT OPEN orders reserve risk_capital(limit_net_price, spread_width, qty).
    """
    if o.intent != "OPEN":
        return 0.0
    net = o.limit_net_price if o.limit_net_price is not None else 0.0
    return max(0.0, risk_capital(net, spread_width(o.legs), o.quantity))


def buying_power(acct: OptionsPaperAccount) -> float:
    """Available buying power for new option positions.

    buying_power = cash
                 − reserved_collateral   (collateral held against open credit positions)
                 − Σ(_open_reservation)  (pending OPEN order risk capital)
    """
    return (
        acct.cash
        - acct.reserved_collateral
        - sum(_open_reservation(o) for o in acct.open_orders)
    )


def _validate_structure(strategy: str, legs: list[OptionLeg], intent: str) -> None:
    """Validate the structural invariants of the order before placement.

    SINGLE: exactly 1 leg; naked sell-to-open is rejected (defined-risk rule).
    VERTICAL: delegates to safety.build_option_leg + validate_option_combo (same
              underlying/expiration/type, different strikes, one BUY + one SELL).
    """
    if strategy == "SINGLE":
        if len(legs) != 1:
            raise OptionsPaperError("SINGLE requires exactly 1 leg")
        if intent == "OPEN" and legs[0].side != "BUY":
            raise OptionsPaperError("a single must be bought to open (no naked short single)")
    elif strategy == "VERTICAL":
        # Reuse the real ticket's combo validation (same underlying/expiration/type,
        # different strikes, equal qty, one BUY + one SELL). Use MARKET to avoid
        # the limit_price requirement in validate_option_combo — we only care about
        # structural invariants here, not price deviation.
        built = safety.build_option_combo(strategy="VERTICAL", legs=[
            safety.build_option_leg(
                symbol=lg.occ, side=lg.side, quantity="1", order_type="MARKET"
            )
            for lg in legs
        ])
        safety.validate_option_combo(built)
    else:
        raise OptionsPaperError(f"unsupported strategy {strategy!r}")


def place(
    acct: OptionsPaperAccount,
    *,
    strategy: str,
    legs: list[dict],
    quantity: int,
    intent: str,
    order_type: str,
    time_in_force: str,
    now_iso: str,
    today_et: str,
    marks: dict,
    close_unit_id: str | None = None,
    limit_net_price: float | None = None,
) -> tuple[OptionsPaperAccount, OptionPaperOrder]:
    """Validate and queue a new options paper order.

    Raises OptionsPaperError (subclass of OrderValidationError → HTTP 400) for:
    - Non-positive quantity
    - Invalid OCC symbols
    - Naked single sell-to-open (defined-risk rule)
    - Invalid VERTICAL leg structure (via safety.validate_option_combo)
    - Unsupported strategy
    - Missing close_unit_id for CLOSE orders, or quantity exceeds open position
    - Insufficient buying power for OPEN orders
    - No market price available for MARKET OPEN orders

    The order is appended to acct.open_orders with status="pending"; acct is returned
    mutated (caller holds the updated reference).
    """
    qty = int(quantity)
    if qty <= 0:
        raise OptionsPaperError(f"quantity must be a positive integer, got {quantity!r}")

    leg_list = leg_objs(legs)
    _validate_structure(strategy, leg_list, intent)

    if intent == "CLOSE":
        unit = next((u for u in acct.positions if u.unit_id == close_unit_id), None)
        if unit is None:
            raise OptionsPaperError(f"no open position {close_unit_id!r} to close")
        if qty > unit.quantity:
            raise OptionsPaperError(
                f"cannot close {qty}: only {unit.quantity} contracts open"
            )
        # Mirror the OPEN branch: a LIMIT with no price would rest and then TypeError in
        # _fills_now on every refresh — reject at placement instead (clean 400).
        if order_type == "LIMIT" and limit_net_price is None:
            raise OptionsPaperError("a LIMIT close requires a limit_net_price")
    else:  # OPEN — affordability check: reserve risk capital
        if order_type == "LIMIT":
            net = float(limit_net_price) if limit_net_price is not None else None
        else:
            net = net_price(leg_list, marks, fill=True)
        if net is None:
            raise OptionsPaperError("no market price available for one or more legs")
        need = risk_capital(net, spread_width(leg_list), qty)
        bp = buying_power(acct)
        if need > bp + _EPS:
            raise OptionsPaperError(
                f"insufficient buying power: need {need:.2f}, have {bp:.2f}"
            )

    order = OptionPaperOrder(
        paper_order_id=uuid.uuid4().hex,
        strategy=strategy,
        legs=leg_list,
        quantity=qty,
        intent=intent,
        close_unit_id=close_unit_id,
        order_type=order_type,
        limit_net_price=(
            float(limit_net_price)
            if (order_type == "LIMIT" and limit_net_price is not None)
            else None
        ),
        time_in_force=time_in_force,
        status="pending",
        created_at=now_iso,
        placed_et_date=today_et,
    )
    acct.open_orders.append(order)
    acct.updated_at = now_iso
    return acct, order


# ── fill engine (Task 4) ───────────────────────────────────────────────────────

def _apply_close(acct: OptionsPaperAccount, o: OptionPaperOrder, net: float, now_iso: str) -> None:
    unit = next((u for u in acct.positions if u.unit_id == o.close_unit_id), None)
    if unit is None:
        raise OptionsPaperError(f"position {o.close_unit_id!r} no longer open")
    closed = min(o.quantity, unit.quantity)
    value = -net                                       # cash received per contract on close
    pnl = (value - unit.avg_net_price) * _MULT * closed
    acct.realized_pnl += pnl
    acct.cash += value * _MULT * closed
    released = unit.collateral * (closed / unit.quantity) if unit.quantity else 0.0
    acct.reserved_collateral -= released
    # Stamp the close order so the Journal can build a closed options trade (see options_schema).
    o.realized_pnl = pnl
    o.open_net_price = unit.avg_net_price
    o.close_value = value
    o.closed_qty = closed
    o.unit_opened_at = unit.opened_at
    o.risk_basis = unit.avg_net_price * _MULT * closed if unit.avg_net_price >= 0 else released
    unit.collateral -= released
    unit.quantity -= closed
    if unit.quantity <= 0:
        acct.positions = [u for u in acct.positions if u.unit_id != unit.unit_id]


def _intrinsic(leg: OptionLeg, spot: float) -> float:
    """Per-leg intrinsic value at expiry: CALL max(0, spot−strike), PUT max(0, strike−spot)."""
    if leg.option_type == "CALL":
        return max(0.0, spot - leg.strike)
    return max(0.0, leg.strike - spot)


def _settle_expirations(acct: OptionsPaperAccount, spots: dict, now_iso: str, today_et: str,
                        settle_spots: dict | None = None) -> OptionsPaperAccount:
    """Settle all expired position units to intrinsic value (mutates acct in place).

    A unit is expired when min(leg.expiration) < today_et.
    Settlement prices intrinsic at, in preference order:
      1. settle_spots[(underlying, exp_date)] — the expiration-day close when the caller can
         supply it (settle_basis="exp_close");
      2. spots[underlying] — the CURRENT spot at whatever refresh settles it, an approximation
         when the account isn't refreshed on expiration day (settle_basis="current_spot").
    If neither price is available, the unit is deferred to the next cycle.

    Key canonicalization: `unit.legs[0].underlying` is always an OCC-parsed root
    (`options_chain.occ_root`, e.g. "BRKB" for Berkshire class B), but callers may key `spots`/
    `settle_spots` off the plain universe/position symbol instead ("BRK B", with the space —
    e.g. options_entry_service.py's `spots_for({sym})` where `sym` comes straight from
    `rsi2.UNIVERSE`). Both dicts are re-keyed through `occ_root` before lookup so either form
    resolves; this is a no-op for every existing single-word ticker (occ_root(x) == x for them),
    so already-stored paper units (data/paper/options.json) keep resolving unchanged.
    Unit settlement value = Σ(BUY leg intrinsic) − Σ(SELL leg intrinsic).
    realized_pnl += (value − avg_net_price) * 100 * qty
    cash += value * 100 * qty
    reserved_collateral -= unit.collateral
    The settled unit is appended to history as an 'expired' OptionPaperOrder stamped with
    settle_basis so the Journal row is honest about which price it used.
    """
    canon_spots = {options_chain.occ_root(k): v for k, v in (spots or {}).items()}
    canon_settle_spots = {(options_chain.occ_root(k), e): v
                          for (k, e), v in (settle_spots or {}).items()}
    survivors: list[OptionPositionUnit] = []
    for unit in acct.positions:
        exp = min(leg.expiration for leg in unit.legs)
        if exp >= today_et:                       # not yet expired
            survivors.append(unit)
            continue
        under = unit.legs[0].underlying
        spot = canon_settle_spots.get((under, exp))
        basis = "exp_close" if spot is not None else "current_spot"
        if spot is None:
            spot = canon_spots.get(under)
        if spot is None:                          # can't settle without the underlying — defer
            survivors.append(unit)
            continue
        value = sum((_intrinsic(lg, spot) if lg.side == "BUY" else -_intrinsic(lg, spot))
                    for lg in unit.legs)
        pnl = (value - unit.avg_net_price) * _MULT * unit.quantity
        acct.realized_pnl += pnl
        acct.cash += value * _MULT * unit.quantity
        acct.reserved_collateral -= unit.collateral
        risk_basis = (unit.avg_net_price * _MULT * unit.quantity
                      if unit.avg_net_price >= 0 else unit.collateral)
        acct.history.append(OptionPaperOrder(
            paper_order_id=uuid.uuid4().hex, strategy=unit.strategy, legs=unit.legs,
            quantity=unit.quantity, intent="CLOSE", order_type="MARKET", status="expired",
            created_at=now_iso, placed_et_date=today_et, filled_at=now_iso, fill_net_price=value,
            realized_pnl=pnl, open_net_price=unit.avg_net_price, close_value=value,
            closed_qty=unit.quantity, unit_opened_at=unit.opened_at, risk_basis=risk_basis,
            settle_basis=basis))
    acct.positions = survivors
    return acct


def _fills_now(o: OptionPaperOrder, current_net: float) -> bool:
    """A LIMIT fills when the current net is at least as favorable as the limit (lower = better,
    for both debit and credit since credit nets are negative). MARKET always fills."""
    if o.order_type == "MARKET":
        return True
    return current_net <= o.limit_net_price + _EPS


def buying_power_excluding(acct: OptionsPaperAccount, o: OptionPaperOrder) -> float:
    """Buying power ignoring order o's own reservation (it's the one being filled)."""
    return acct.cash - acct.reserved_collateral - sum(
        _open_reservation(x) for x in acct.open_orders if x.paper_order_id != o.paper_order_id)


def _apply_open(acct: OptionsPaperAccount, o: OptionPaperOrder, net: float, now_iso: str) -> None:
    width = spread_width(o.legs)
    # Same clamp as risk_capital: a credit larger than the width must not book NEGATIVE
    # collateral (which would inflate buying power).
    collateral = 0.0 if net >= 0 else max(0.0, (width - abs(net)) * _MULT * o.quantity)
    acct.cash -= net * _MULT * o.quantity            # pay debit / receive credit
    acct.reserved_collateral += collateral
    acct.positions.append(OptionPositionUnit(
        unit_id=uuid.uuid4().hex, strategy=o.strategy, legs=o.legs, quantity=o.quantity,
        avg_net_price=net, collateral=collateral, opened_at=now_iso))


def evaluate(
    acct: OptionsPaperAccount,
    *,
    marks: dict,
    spots: dict,
    now_iso: str,
    today_et: str,
    settle_spots: dict | None = None,
) -> tuple[OptionsPaperAccount, list[OptionPaperOrder]]:
    """Advance open orders against current marks; fill eligible orders into positions.

    Call order: settle expirations first (Task 6 stub = no-op), then fill.
    settle_spots (optional) maps (underlying, exp_date) -> expiration-day close; see
    _settle_expirations for the price-basis rules.
    Returns (updated acct, list of filled orders).
    """
    fills: list[OptionPaperOrder] = []
    _settle_expirations(acct, spots, now_iso, today_et, settle_spots=settle_spots)
    still_open: list[OptionPaperOrder] = []
    for o in acct.open_orders:
        if o.time_in_force == "DAY" and o.placed_et_date < today_et:
            o.status = "expired"
            acct.history.append(o)
            continue
        net = net_price(o.legs, marks, fill=True)
        if net is None or not _fills_now(o, net):
            still_open.append(o)
            continue
        if o.intent == "OPEN":
            need = risk_capital(net, spread_width(o.legs), o.quantity)
            if need > buying_power_excluding(acct, o) + _EPS:   # no longer affordable
                o.status = "rejected"
                o.reject_reason = "insufficient buying power at fill time"
                acct.history.append(o)
                continue
            _apply_open(acct, o, net, now_iso)
        else:
            # The unit can be gone by fill time (settled by the expiration pass above, or fully
            # closed by an earlier order this pass) — reject like the equity engine, never raise
            # (a raise here would wedge every subsequent account refresh in a 400 loop).
            unit = next((u for u in acct.positions if u.unit_id == o.close_unit_id), None)
            if unit is None:
                o.status = "rejected"
                o.reject_reason = f"position {o.close_unit_id!r} no longer open"
                acct.history.append(o)
                continue
            _apply_close(acct, o, net, now_iso)               # defined in Task 5
        o.status = "filled"
        o.fill_net_price = net
        o.filled_at = now_iso
        acct.history.append(o)
        fills.append(o)
    acct.open_orders = still_open
    acct.updated_at = now_iso
    return acct, fills


# ── cancel / trim_history / account_view (Task 7) ────────────────────────────

def cancel(acct: OptionsPaperAccount, paper_order_id: str) -> OptionsPaperAccount:
    """Cancel a pending open order by id. Moves it to history with status 'cancelled'."""
    rest = []
    for o in acct.open_orders:
        if o.paper_order_id == paper_order_id:
            o.status = "cancelled"
            acct.history.append(o)
        else:
            rest.append(o)
    acct.open_orders = rest
    return acct


def trim_history(acct: OptionsPaperAccount, cap: int) -> OptionsPaperAccount:
    """Keep only the most recent `cap` history entries (no-op when cap < 0)."""
    if cap >= 0 and len(acct.history) > cap:
        acct.history = acct.history[-cap:]
    return acct


def account_view(acct: OptionsPaperAccount, marks: dict) -> dict:
    """Mark all positions at mid and return a snapshot dict.

    Per position unit:
        mark_net    = net_price(legs, marks, fill=False)   # net mid (debit basis)
        value       = mark_net                             # per-contract close value at mid
        market_value = value * 100 * qty
        unrealized_pnl = (value − avg_net_price) * 100 * qty
    net_liquidation = cash + Σ market_value — positions with no mark are OMITTED from the sum
    (a credit-spread liability could be dropped), so unmarked_positions discloses the count.
    """
    positions = []
    market_total = 0.0
    unmarked = 0
    for u in acct.positions:
        mark = net_price(u.legs, marks, fill=False)        # net mid (debit basis)
        # Closing reverses the legs, so close_net = −mark and the close value
        # (= −close_net) = mark.  value = mark directly.
        value = mark if mark is not None else None         # per-contract close value at mid
        mv = value * _MULT * u.quantity if value is not None else None
        upnl = (value - u.avg_net_price) * _MULT * u.quantity if value is not None else None
        if mv is not None:
            market_total += mv
        else:
            unmarked += 1
        positions.append({
            "unit_id": u.unit_id, "strategy": u.strategy, "quantity": u.quantity,
            "avg_net_price": u.avg_net_price, "collateral": u.collateral,
            "mark_net": mark, "market_value": mv, "unrealized_pnl": upnl,
            "legs": [lg.model_dump() for lg in u.legs],
        })
    return {
        "account_id": acct.account_id,
        "starting_cash": acct.starting_cash,
        "cash": acct.cash,
        "reserved_collateral": acct.reserved_collateral,
        "buying_power": buying_power(acct),
        "realized_pnl": acct.realized_pnl,
        "net_liquidation": acct.cash + market_total,
        "unmarked_positions": unmarked,   # units excluded from net_liquidation (no mark)
        "positions": positions,
        "open_orders": [o.model_dump() for o in acct.open_orders],
        "history": [o.model_dump() for o in acct.history[-50:]],
        "updated_at": acct.updated_at,
    }
