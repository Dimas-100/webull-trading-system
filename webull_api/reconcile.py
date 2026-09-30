"""Protective-stop reconcile: detect open long-equity positions with NO resting protective SELL stop,
and build the protective STOP_LOSS (market-on-trigger) to draft. Pure rule layer + an injectable scan(),
mirroring webull_api/exits.py. Read + draft-only — never places (drafting/placement is downstream,
behind the unchanged submit gate)."""
from __future__ import annotations

from dataclasses import dataclass

from webull_api import portfolio, safety, trading
from webull_api._util import num as _num

_STOP_TYPES = {"STOP_LOSS", "STOP_LOSS_LIMIT"}

# Quantity keys a working order leg may carry back from the broker. The response shape is
# unverified (the SDK models only requests; the live open-orders book was empty when this was
# written), so we read a candidate set and fall back safely when none parse — see is_stop_protected.
_ORDER_QTY_KEYS = ("quantity", "qty", "totalQuantity", "total_quantity", "orderQuantity", "order_quantity")
_STOP_COVERAGE_EPS = 1e-6


def _rows(raw) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("data"), list):
        return raw["data"]
    return []


def _open_orders_known(raw) -> bool:
    """True only for a shape _rows() can trust to mean what it returns: a bare list (even
    empty — that legitimately means "no open orders"), or a dict envelope with a list under
    "data". Anything else is UNRECOGNISED, not "flat empty" — a rate-limit envelope
    ({"code": "RATE_LIMIT", ...}) or an HTML/gateway error body (the fallback dict
    trading._safe_json returns when res.json() raises) both come back as a normal 200 with
    no exception raised, and _rows() silently degrades either to []. Read that way, a
    working sell hiding at the broker becomes invisible and the fractional synthetic path
    fires a duplicate MARKET SELL. Kept in lockstep with _rows(): every shape _rows() treats
    as real data must return True here; every shape it degrades to [] must return False."""
    if isinstance(raw, list):
        return True
    return isinstance(raw, dict) and isinstance(raw.get("data"), list)


def _iter_legs(open_orders):
    """Yield each order/leg dict, flattening Webull's combo envelope ({"orders": [leg, ...]})."""
    for o in _rows(open_orders):
        if not isinstance(o, dict):
            continue
        legs = o.get("orders")
        if isinstance(legs, list) and legs:
            for leg in legs:
                if isinstance(leg, dict):
                    yield leg
        else:
            yield o


def stop_covered_qty(open_orders, symbol: str) -> tuple[float, bool]:
    """(covered_qty, all_parsed) across working SELL STOP_LOSS/STOP_LOSS_LIMIT legs for `symbol`.

    covered_qty sums the quantity of every matching stop leg whose quantity parses. all_parsed is
    False when at least one matching stop leg exists whose quantity could NOT be parsed — the
    signal that covered_qty is a LOWER BOUND, not the true coverage."""
    sym = (symbol or "").strip().upper()
    covered = 0.0
    all_parsed = True
    for leg in _iter_legs(open_orders):
        lsym = str(leg.get("symbol") or leg.get("ticker") or "").strip().upper()
        side = str(leg.get("side") or "").strip().upper()
        otype = str(leg.get("order_type") or leg.get("orderType") or "").strip().upper()
        if lsym == sym and side == "SELL" and otype in _STOP_TYPES:
            q = _num(leg, _ORDER_QTY_KEYS)
            if q is None:
                all_parsed = False
            else:
                covered += q
    return covered, all_parsed


def resting_stop_orders(open_orders, symbol: str) -> list[dict]:
    """Working SELL stop legs for `symbol` — the orders a CLOSING sell must clear first.

    A resting protective stop commits the shares, so an additional SELL is rejected
    `417 OAUTH_OPENAPI_ORDER_NOT_SUPPORT_REVERSE_OPTION` (observed live 2026-08-13 and
    2026-08-14). Returns the legs themselves so the caller can read `client_order_id` and cancel.
    """
    sym = (symbol or "").strip().upper()
    out: list[dict] = []
    for leg in _iter_legs(open_orders):
        lsym = str(leg.get("symbol") or leg.get("ticker") or "").strip().upper()
        side = str(leg.get("side") or "").strip().upper()
        otype = str(leg.get("order_type") or leg.get("orderType") or "").strip().upper()
        if lsym == sym and side == "SELL" and otype in _STOP_TYPES:
            out.append(leg)
    return out


def is_stop_protected(open_orders, symbol: str, held_qty) -> bool:
    """True when the resting SELL stop(s) for `symbol` cover the held quantity.

    Replaces the old quantity-BLIND presence check: a stop covering fewer shares than held used to
    read as full protection, masking an under-protected position from scan_unprotected.

      - no working SELL stop            -> False   (as the old presence check also returned False)
      - stops exist, all qty parsed     -> covered + EPS >= held_qty
      - a matching stop qty UNPARSEABLE -> True     (fall back to presence-based coverage: we can't
                                                     PROVE under-coverage, so we must not regress
                                                     into drafting a duplicate stop — see the spec)

    The broker's working-order quantity field is unverified; the fallback makes that safe. This
    only ever DOWNGRADES protected->unprotected on a positively-parsed shortfall."""
    covered, all_parsed = stop_covered_qty(open_orders, symbol)
    if covered <= 0.0 and all_parsed:
        return False
    if not all_parsed:
        return True
    try:
        held = float(held_qty)
    except (TypeError, ValueError):
        return True   # can't compare -> don't claim under-coverage
    return covered + _STOP_COVERAGE_EPS >= held


def closing_sell_covers(open_orders, symbol: str, held_qty) -> bool:
    """True when working NON-stop SELL leg(s) for `symbol` cover the held quantity.

    The cancel-then-sell exit leaves a window (17:45 -> next open) where the position rests
    with NO stop but a working MARKET/LIMIT SELL: it is already on its way out. A later run
    drafting a stop there would stack an order onto shares the sell has committed (417).
    Mirrors is_stop_protected's fallback: an unparseable working-sell qty falls back to
    presence-based coverage rather than claiming an under-coverage it cannot prove."""
    sym = (symbol or "").strip().upper()
    covered, all_parsed, seen = 0.0, True, False
    for leg in _iter_legs(open_orders):
        lsym = str(leg.get("symbol") or leg.get("ticker") or "").strip().upper()
        side = str(leg.get("side") or "").strip().upper()
        otype = str(leg.get("order_type") or leg.get("orderType") or "").strip().upper()
        if lsym == sym and side == "SELL" and otype not in _STOP_TYPES:
            seen = True
            q = _num(leg, _ORDER_QTY_KEYS)
            if q is None:
                all_parsed = False
            else:
                covered += q
    if not seen:
        return False
    if not all_parsed:
        return True
    try:
        held = float(held_qty)
    except (TypeError, ValueError):
        return True   # can't compare -> don't claim under-coverage
    return covered + _STOP_COVERAGE_EPS >= held


def has_working_sell(open_orders, symbol: str) -> bool:
    """True if ANY working SELL leg for `symbol` exists, whatever its order type.

    is_stop_protected()/stop_covered_qty() only see STOP_LOSS/STOP_LOSS_LIMIT legs, so a
    synthetic MARKET sell placed by an EARLIER run is invisible to them. The synthetic path
    must see those, or it would place a second market sell on a position it has already sold."""
    sym = (symbol or "").strip().upper()
    for leg in _iter_legs(open_orders):
        lsym = str(leg.get("symbol") or leg.get("ticker") or "").strip().upper()
        side = str(leg.get("side") or "").strip().upper()
        if lsym == sym and side == "SELL":
            return True
    return False


def is_fractional(qty) -> bool:
    """True when the position is not a whole number of shares. Webull refuses to REST any
    stop order on such a position (417 OAUTH_OPENAPI_FRACTION_ONLY_ALLOW_MARKET), so these
    positions cannot be protected the normal way. Unparseable -> False, which routes to the
    unchanged whole-share path (no behaviour change on junk input)."""
    try:
        return not float(qty).is_integer()
    except (TypeError, ValueError):
        return False


def _qty_str(qty) -> str:
    f = float(qty)
    return str(int(f)) if f.is_integer() else str(f)


def protective_order(symbol: str, qty, stop_price) -> dict:
    """A GTC STOP_LOSS (market-on-trigger) SELL at the protective level. Market-on-trigger so the stop
    is guaranteed to FILL on a gap-down — the scenario protection exists for — even at an unbounded fill
    price (an acceptable cost on the liquid, small proof-phase positions; a stop-limit that doesn't fill
    fails at the stop's one job). The draft path (data/intents/, placed by hand) accepts a bare
    STOP_LOSS; protection is never routed through the codeword place_order path (which refuses an
    unpriceable STOP_LOSS). Validated (no last_price -> structural validation only)."""
    stop = round(float(stop_price), 2)
    order = safety.build_order(symbol=symbol, side="SELL", quantity=_qty_str(qty),
                               order_type="STOP_LOSS", stop_price=str(stop), time_in_force="GTC")
    safety.validate_order(order)
    return order


def synthetic_stop_order(symbol: str, qty, stop_price, last) -> dict | None:
    """The stand-in for a resting stop on a FRACTIONAL position.

    Webull rejects a resting STOP_LOSS on a fractional quantity (verified 2026-07-22, both
    in-session and after the close), so there is no order that both rests and is accepted.
    Autopilot therefore evaluates the level itself on each run: at/below the stop -> a
    full-size MARKET SELL; still above it -> None.

    MARKET rather than a marketable LIMIT for the reason protective_order already gives —
    a stop that doesn't fill fails at the stop's one job. MARKET is verified PREVIEW-accepted
    for fractional quantities with the market both open and shut (2026-07-22); preview only
    validates shape/eligibility, so live after-hours behaviour — whether a DAY market order
    submitted after the close queues or is dropped — remains unproven."""
    if stop_price is None or last is None:
        return None
    if float(last) > float(stop_price):
        return None
    order = safety.build_order(symbol=symbol, side="SELL", quantity=_qty_str(qty),
                               order_type="MARKET", time_in_force="DAY")
    safety.validate_order(order)
    return order


def synthetic_decision(open_orders, oo_ok: bool, symbol: str, qty, stop_price, last):
    """(order, skip_reason) for a FRACTIONAL position — exactly one of the two is non-None.

    Fails closed in the OPPOSITE direction from the resting-stop path. scan_unprotected
    drafts a resting stop even when open orders are unreadable, because a missing stop is
    the dangerous failure and a duplicate resting stop costs nothing. A synthetic sell
    EXECUTES, so its dangerous failure is the reverse — selling a position twice. Unknown
    state therefore means do nothing, and every skip carries a reason for the audit log."""
    if not oo_ok:
        return None, "fractional: open orders unreadable"
    if has_working_sell(open_orders, symbol):
        return None, "fractional: sell already working"
    if last is None:
        return None, "fractional: last price unknown"
    if stop_price is None:
        return None, "fractional: no stop level"
    order = synthetic_stop_order(symbol, qty, stop_price, last)
    if order is None:
        return None, "fractional: monitored, above stop"
    return order, None


_SYMBOL_KEYS = ("symbol", "ticker")
_QTY_KEYS = ("quantity", "qty", "position", "shares")
_COST_KEYS = ("cost_price", "costPrice", "avgCost", "averageCost", "unitCost", "unit_cost")
_LAST_KEYS = ("last_price", "lastPrice")


def _str(row, keys) -> str:
    for k in keys:
        v = row.get(k)
        if v is not None and v != "":
            return str(v)
    return ""


def _is_option(row) -> bool:
    return str(row.get("instrument_type") or row.get("asset_type") or "").upper() == "OPTION"


@dataclass
class Unprotected:
    symbol: str
    qty: float
    cost_basis: float | None
    last: float | None
    stop_price: float | None
    stop_source: str            # "structural" | "backstop" | "manual" | "error"
    protective: dict | None     # the SELL STOP_LOSS to draft, or None (needs manual)
    error: str | None = None    # per-position failure (isolated, does not abort the scan)
    fractional: bool = False    # qty is not whole -> the broker will not rest a stop on it
    synthetic: dict | None = None   # fractional only: the MARKET SELL to place NOW
    skip_reason: str | None = None  # fractional only: why no synthetic order was built
    rejected_plan_stop: float | None = None  # a structural stop refused by plan_stop_is_sane


# A plan's structural stop must sit BELOW cost (it is a stop) but within a plausible distance of
# it. The bound exists because a stale plan silently outranks the percentage backstop: on
# 2026-08-13 a 2026-07-07 artifact (structural_stop 28.0, written when the entry band capped near
# $80) put a $28.00 GTC stop under a $303 AAPL share — protection that triggers at -91%, i.e. none.
# 0.50 is deliberately loose: it is an absurdity filter, not a risk policy. The -8% backstop, not
# this bound, is what actually sizes protection.
_MIN_PLAN_STOP_FRACTION = 0.50


def plan_stop_is_sane(stop_price, cost) -> bool:
    """True when a plan-of-record structural stop is usable for `cost`.

    Pure. Unknown/unparseable inputs are NOT sane — the caller falls back to the percentage
    backstop, which is always computable from cost. Rejecting is safe (you get a backstop);
    accepting a bad value is not (you get a stop that never fires).

    NOTE: the upper bound is `cost` because plans are written at entry, when the structural stop
    sits below it. Revisit when trailing stops exist — a raised breakeven-plus stop is legitimate
    and would be rejected here.
    """
    if stop_price is None or cost in (None, 0):
        return False
    try:
        stop, basis = float(stop_price), float(cost)
    except (TypeError, ValueError):
        return False
    if basis <= 0 or stop <= 0:
        return False
    return basis * _MIN_PLAN_STOP_FRACTION <= stop <= basis


def scan_unprotected(account_id, *, plans=None, stop_loss_pct: float = 8.0,
                     get_positions=portfolio.get_positions,
                     get_open_orders=trading.get_open_orders) -> list[Unprotected]:
    """Long-equity positions with NO resting protective SELL stop. Uses each position's plan-of-record
    structural stop when known, else a cost-basis percentage backstop. If open orders can't be read we
    cannot prove protection, so we conservatively draft (a missing stop is the dangerous failure).
    FRACTIONAL positions invert that: their synthetic stop EXECUTES rather than resting, so unknown
    state must not produce an order — see synthetic_decision."""
    plans = plans or {}
    raw = get_positions(account_id)
    try:
        open_orders = get_open_orders(account_id)
        oo_ok = _open_orders_known(open_orders)
    except Exception:
        open_orders, oo_ok = [], False

    rows: list[Unprotected] = []
    for pos in (raw if isinstance(raw, list) else []):
        if not isinstance(pos, dict) or _is_option(pos):
            continue
        symbol = _str(pos, _SYMBOL_KEYS)
        qty = _num(pos, _QTY_KEYS)
        if not symbol or qty is None or qty <= 0:
            continue
        if oo_ok and is_stop_protected(open_orders, symbol, qty):
            continue
        if oo_ok and not is_fractional(qty) and closing_sell_covers(open_orders, symbol, qty):
            continue   # already exiting — a stop would stack onto shares the sell committed
                       # (fractional keeps its own audit-traced suppression in synthetic_decision)
        try:
            cost = _num(pos, _COST_KEYS)
            last = _num(pos, _LAST_KEYS)
            plan = plans.get(symbol) or plans.get(symbol.strip().upper()) or {}
            stop_price, source = None, "manual"
            planned, rejected = plan.get("structural_stop"), None
            if planned and plan_stop_is_sane(planned, cost):
                stop_price, source = round(float(planned), 2), "structural"
            elif cost:
                stop_price, source = round(float(cost) * (1 - stop_loss_pct / 100.0), 2), "backstop"
                if planned:
                    # Loud on purpose: a rejected plan stop means the plan-of-record is wrong and
                    # the position is running on the generic backstop until someone fixes it.
                    source = "backstop:plan-rejected"
                    try:
                        rejected = round(float(planned), 2)
                    except (TypeError, ValueError):
                        rejected = None
            if is_fractional(qty):
                # No resting stop is possible here; autopilot evaluates the level itself.
                synth, skip = synthetic_decision(open_orders, oo_ok, symbol, qty, stop_price, last)
                rows.append(Unprotected(symbol, qty, cost, last, stop_price, source, None,
                                        fractional=True, synthetic=synth, skip_reason=skip,
                                        rejected_plan_stop=rejected))
                continue
            protective = protective_order(symbol, qty, stop_price) if stop_price is not None else None
            rows.append(Unprotected(symbol, qty, cost, last, stop_price, source, protective,
                                    rejected_plan_stop=rejected))
        except Exception as e:
            rows.append(Unprotected(symbol, qty, None, None, None, "error", None, error=str(e)[:120]))
    return rows
