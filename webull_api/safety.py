"""Pure order construction, validation, and the submit gate. No network, no SDK calls."""
from __future__ import annotations

import uuid

VALID_SIDES = {"BUY", "SELL"}            # v1: SHORT deferred
# Webull order_type member names. STOP_LOSS = stop -> market on trigger; STOP_LOSS_LIMIT = stop + limit.
# (Direction is by side: a BUY stop above market is a buy-stop entry; a SELL stop below is protective.)
VALID_ORDER_TYPES = {"MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_LIMIT"}
STOP_ORDER_TYPES = {"STOP_LOSS", "STOP_LOSS_LIMIT"}
VALID_TIF = {"DAY", "GTC"}
DEFAULT_SESSION = "CORE"


class OrderValidationError(ValueError):
    """Raised when an order fails a pre-submit sanity check."""


def build_order(*, symbol, side, quantity, order_type="LIMIT", limit_price=None, stop_price=None,
                time_in_force="DAY", support_trading_session=DEFAULT_SESSION,
                client_order_id=None) -> dict:
    """Assemble a well-formed simple US-equity order dict (strings, as the API expects)."""
    order = {
        "client_order_id": client_order_id or uuid.uuid4().hex,
        "combo_type": "NORMAL",  # required by the live API for a simple order
        "symbol": str(symbol).strip().upper(),
        "instrument_type": "EQUITY",
        "market": "US",
        "order_type": order_type,
        "quantity": str(quantity),
        "support_trading_session": support_trading_session,
        "side": side,
        "time_in_force": time_in_force,
        "entrust_type": "QTY",
    }
    if limit_price is not None:
        order["limit_price"] = str(limit_price)
    if stop_price is not None:
        order["stop_price"] = str(stop_price)
    return order


def validate_order(order: dict, *, last_price=None, max_price_deviation: float = 0.20) -> dict:
    """Raise OrderValidationError if unsafe/malformed; return the order on success."""
    if order.get("side") not in VALID_SIDES:
        raise OrderValidationError(f"side must be one of {sorted(VALID_SIDES)}, got {order.get('side')!r}")
    otype = order.get("order_type")
    if otype not in VALID_ORDER_TYPES:
        raise OrderValidationError(f"order_type must be one of {sorted(VALID_ORDER_TYPES)}, got {otype!r}")
    if order.get("time_in_force") not in VALID_TIF:
        raise OrderValidationError(
            f"time_in_force must be one of {sorted(VALID_TIF)}, got {order.get('time_in_force')!r}")
    if not str(order.get("symbol") or "").strip():
        raise OrderValidationError("symbol must be non-empty")
    try:
        qty = float(order.get("quantity"))
    except (TypeError, ValueError):
        raise OrderValidationError(f"quantity must be numeric, got {order.get('quantity')!r}")
    if qty <= 0:
        raise OrderValidationError(f"quantity must be > 0, got {qty}")

    lp = order.get("limit_price")
    sp = order.get("stop_price")
    side = order.get("side")

    def _pos(v, name):
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise OrderValidationError(f"{name} must be numeric, got {v!r}")
        if f <= 0:
            raise OrderValidationError(f"{name} must be > 0, got {f}")
        return f

    def _deviation_guard(price, name):
        if last_price is not None and last_price > 0:
            dev = abs(price - last_price) / last_price
            if dev > max_price_deviation:
                raise OrderValidationError(
                    f"{name} {price} is {dev:.0%} away from last {last_price} "
                    f"(> {max_price_deviation:.0%} guard); pass max_price_deviation to override")

    def _side_guard(stop):
        # A stop on the wrong side of the market would trigger immediately as a market order.
        if last_price is not None and last_price > 0:
            if side == "BUY" and stop < last_price:
                raise OrderValidationError(
                    f"a BUY stop must trigger at/above the last price ({stop} < {last_price}); "
                    "a stop below the market fills immediately as a market order")
            if side == "SELL" and stop > last_price:
                raise OrderValidationError(
                    f"a SELL stop must trigger at/below the last price ({stop} > {last_price})")

    if otype == "LIMIT":
        if lp is None:
            raise OrderValidationError("limit_price is required for LIMIT orders")
        _deviation_guard(_pos(lp, "limit_price"), "limit_price")
        if sp is not None:
            raise OrderValidationError("LIMIT order must not carry a stop_price")
    elif otype == "MARKET":
        if lp is not None:
            raise OrderValidationError("MARKET order must not carry a limit_price")
        if sp is not None:
            raise OrderValidationError("MARKET order must not carry a stop_price")
    elif otype == "STOP_LOSS":
        if sp is None:
            raise OrderValidationError("stop_price is required for STOP_LOSS orders")
        if lp is not None:
            raise OrderValidationError("STOP_LOSS must not carry a limit_price (use STOP_LOSS_LIMIT)")
        spf = _pos(sp, "stop_price")
        _deviation_guard(spf, "stop_price")
        _side_guard(spf)
    elif otype == "STOP_LOSS_LIMIT":
        if sp is None or lp is None:
            raise OrderValidationError("STOP_LOSS_LIMIT requires both stop_price and limit_price")
        spf = _pos(sp, "stop_price")
        _deviation_guard(spf, "stop_price")
        _deviation_guard(_pos(lp, "limit_price"), "limit_price")
        _side_guard(spf)
    return order


# ── Options (Phase 2) ─────────────────────────────────────────────────────────
# Parallel to the equity builders/validation above. The submit GATE (should_submit) is
# reused unchanged by trading.place_option — these only construct + validate the order.
VALID_OPTION_STRATEGIES = {"SINGLE", "VERTICAL"}
_STRATEGY_LEGS = {"SINGLE": 1, "VERTICAL": 2}


def _fmt_strike(strike: float) -> str:
    return str(int(strike)) if float(strike) == int(strike) else str(strike)


def build_option_leg(*, symbol, side, quantity, order_type="LIMIT", limit_price=None,
                     instrument_id=None, time_in_force="DAY", client_order_id=None) -> dict:
    """One option leg from an OCC symbol. The live API wants the UNDERLYING symbol plus
    instrument_super_type/instrument_type/option_type/strike_price/init_exp_date (verified via
    a live non-executing preview), so we expand the OCC here. Bad OCC -> OrderValidationError."""
    from .options_chain import parse_occ

    try:
        info = parse_occ(str(symbol).strip().upper())
    except ValueError:
        raise OrderValidationError(f"invalid option symbol {symbol!r}")
    leg = {
        "client_order_id": client_order_id or uuid.uuid4().hex,
        "symbol": info["root"],
        "instrument_super_type": "OPTION",
        "instrument_type": "OPTION",
        "option_type": "CALL" if info["kind"] == "C" else "PUT",
        "strike_price": _fmt_strike(info["strike"]),
        "init_exp_date": info["exp"].isoformat(),
        "market": "US",
        "order_type": order_type,
        "quantity": str(quantity),
        "side": side,
        "time_in_force": time_in_force,
        "entrust_type": "QTY",
    }
    if instrument_id is not None:
        leg["instrument_id"] = str(instrument_id)
    if limit_price is not None:
        leg["limit_price"] = str(limit_price)
    return leg


def _combo_net(legs) -> float:
    """Net per-share price: + for BUY legs, - for SELL legs (positive = debit)."""
    net = 0.0
    for leg in legs:
        try:
            p = float(leg.get("limit_price") or 0)
        except (TypeError, ValueError):
            p = 0.0
        net += p if leg.get("side") == "BUY" else -p
    return net


def build_option_combo(*, strategy, legs, client_order_id=None) -> dict:
    """Webull combo envelope. It is itself a full order object (order-level side/order_type/
    limit_price/quantity) that contains the legs under `orders`. For SINGLE the order-level
    fields mirror the leg; for VERTICAL the net debit/credit sets side + limit_price."""
    legs = list(legs)
    first = legs[0] if legs else {}
    qty = first.get("quantity", "1")
    tif = first.get("time_in_force", "DAY")
    if strategy == "VERTICAL" and len(legs) == 2:
        net = _combo_net(legs)
        side = "BUY" if net >= 0 else "SELL"
        order_type = "LIMIT"
        limit_price = f"{abs(net):.2f}"
    else:  # SINGLE (or malformed — validate rejects later): mirror the (first) leg
        side = first.get("side", "BUY")
        order_type = first.get("order_type", "LIMIT")
        limit_price = first.get("limit_price")
    combo = {
        "client_order_id": client_order_id or uuid.uuid4().hex,
        "combo_type": "NORMAL",
        "option_strategy": strategy,
        "side": side,
        "order_type": order_type,
        "quantity": str(qty),
        "time_in_force": tif,
        "entrust_type": "QTY",
        "orders": legs,
    }
    if limit_price is not None:
        combo["limit_price"] = str(limit_price)
    return combo


def validate_option_combo(combo: dict, *, last_by_leg: dict | None = None,
                          max_price_deviation: float = 0.70) -> dict:
    """Raise OrderValidationError if the option combo is malformed/unsafe; return it on success.
    Operates on the already-expanded legs produced by build_option_leg (underlying symbol +
    option_type + strike_price + init_exp_date).

    Optional fat-finger guard: when ``last_by_leg`` maps a leg's client_order_id to a reference
    last price, a LIMIT leg whose limit is more than ``max_price_deviation`` away is rejected. The
    default is WIDE (70%) because option premiums swing far more than equities — it is meant to catch
    order-of-magnitude typos ($50 vs $0.50), not legitimate wide spreads. Best-effort: legs without a
    reference price are skipped, so a missing/failed price feed never blocks a legitimate order. This
    only ADDS validation; the submit gate (should_submit) is unchanged."""
    strat = combo.get("option_strategy")
    if strat not in VALID_OPTION_STRATEGIES:
        raise OrderValidationError(
            f"option_strategy must be one of {sorted(VALID_OPTION_STRATEGIES)}, got {strat!r}")
    legs = combo.get("orders") or []
    want = _STRATEGY_LEGS[strat]
    if len(legs) != want:
        raise OrderValidationError(f"{strat} requires {want} leg(s), got {len(legs)}")

    for leg in legs:
        if leg.get("side") not in VALID_SIDES:
            raise OrderValidationError(f"leg side must be one of {sorted(VALID_SIDES)}, got {leg.get('side')!r}")
        ot = leg.get("order_type")
        if ot not in {"LIMIT", "MARKET"}:
            raise OrderValidationError(f"leg order_type must be LIMIT or MARKET, got {ot!r}")
        if leg.get("time_in_force") not in VALID_TIF:
            raise OrderValidationError(f"leg time_in_force must be one of {sorted(VALID_TIF)}")
        if leg.get("instrument_type") != "OPTION":
            raise OrderValidationError("leg instrument_type must be OPTION")
        if leg.get("option_type") not in {"CALL", "PUT"}:
            raise OrderValidationError(f"leg option_type must be CALL or PUT, got {leg.get('option_type')!r}")
        if not str(leg.get("symbol") or "").strip():
            raise OrderValidationError("leg symbol (underlying) must be non-empty")
        if not str(leg.get("init_exp_date") or "").strip():
            raise OrderValidationError("leg init_exp_date must be set")
        try:
            if float(leg.get("strike_price")) <= 0:
                raise OrderValidationError(f"strike_price must be > 0, got {leg.get('strike_price')}")
        except (TypeError, ValueError):
            raise OrderValidationError(f"strike_price must be numeric, got {leg.get('strike_price')!r}")
        try:
            q = float(leg.get("quantity"))
        except (TypeError, ValueError):
            raise OrderValidationError(f"leg quantity must be numeric, got {leg.get('quantity')!r}")
        if q <= 0 or q != int(q):
            raise OrderValidationError(f"leg quantity must be a positive integer, got {leg.get('quantity')!r}")
        lp = leg.get("limit_price")
        if ot == "LIMIT":
            if lp is None:
                raise OrderValidationError("LIMIT leg requires limit_price")
            try:
                if float(lp) <= 0:
                    raise OrderValidationError(f"limit_price must be > 0, got {lp}")
            except (TypeError, ValueError):
                raise OrderValidationError(f"limit_price must be numeric, got {lp!r}")
        elif lp is not None:
            raise OrderValidationError("MARKET leg must not carry a limit_price")

    if strat == "VERTICAL":
        l1, l2 = legs
        if l1.get("symbol") != l2.get("symbol"):
            raise OrderValidationError("vertical legs must share the underlying")
        if l1.get("init_exp_date") != l2.get("init_exp_date"):
            raise OrderValidationError("vertical legs must share the expiration")
        if l1.get("option_type") != l2.get("option_type"):
            raise OrderValidationError("vertical legs must be both calls or both puts")
        if float(l1.get("strike_price")) == float(l2.get("strike_price")):
            raise OrderValidationError("vertical legs must have different strikes")
        if float(l1.get("quantity")) != float(l2.get("quantity")):
            raise OrderValidationError("vertical legs must have equal quantity")
        if {l1.get("side"), l2.get("side")} != {"BUY", "SELL"}:
            raise OrderValidationError("a vertical must have one BUY and one SELL leg")

    if last_by_leg:
        for leg in legs:
            if leg.get("order_type") != "LIMIT":
                continue
            ref = last_by_leg.get(leg.get("client_order_id"))
            if ref is None or ref <= 0:
                continue  # no reference for this leg — skip (never block on a missing price)
            try:
                lp = float(leg.get("limit_price"))
            except (TypeError, ValueError):
                continue
            dev = abs(lp - ref) / ref
            if dev > max_price_deviation:
                raise OrderValidationError(
                    f"option limit {lp} is {dev:.0%} from last {ref} "
                    f"(> {max_price_deviation:.0%} fat-finger guard)")
    return combo


def should_submit(env: str, confirm: bool) -> bool:
    """The submit gate. Submission is allowed ONLY when ``confirm`` is explicitly True.

    The gate is single-factor by design: it returns ``bool(confirm)`` and ignores ``env``.
    ``env`` only selects the endpoint host (and drives the loud prod banner in ``trading.place``);
    it is NOT a second factor here. Because a developer-portal key runs on prod, every
    ``confirm=True`` submit is real money — so the default (``confirm=False`` dry-run) plus the
    CLI's typed ``CONFIRM`` and the web ticket's arm + typed ``CONFIRM`` are what prevent an
    accidental live order. ``env`` is kept in the signature for call-site stability; if a real
    second factor is ever wanted it must only ADD strictness on top of ``bool(confirm)`` (e.g.
    ``confirm and env == "prod"``) and never remove the confirm requirement.
    """
    return bool(confirm)
