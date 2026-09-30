"""The autonomous-placement gate — PURE, fail-closed, side-aware. No network, no I/O.

authorize() is the SINGLE place every unattended placement decision is made. It sits IN FRONT OF
trading.place(confirm=True); it NEVER weakens safety.should_submit. Risk-reducing SELLs (protective
stops, exits) face only the universal layers; risk-ADDING BUY entries face the full cap stack."""
from __future__ import annotations

import math
from dataclasses import dataclass

from webull_api import safety


@dataclass(frozen=True)
class GateState:
    kill_active: bool
    in_window: bool
    open_position_symbols: frozenset
    orders_today: int
    day_pl: float | None          # day P/L $ = unrealized across open positions + today's realized
                                   # LOSS (clamped ≤0, folded in by run.py); None = unknown -> fail-closed.
    spy_risk_off: bool
    last_price: float | None = None
    halt_tripped: bool = False


@dataclass(frozen=True)
class Decision:
    allow: bool
    reason: str
    layer: str


def _notional(order: dict, last: float | None) -> float | None:
    try:
        qty = float(order["quantity"])
    except (TypeError, ValueError, KeyError):
        return None
    lp = order.get("limit_price")
    try:
        px = float(lp) if lp is not None else last
    except (TypeError, ValueError):
        return None
    if px is None:
        return None
    val = qty * px
    # NaN defeats every `> cap` comparison (always False) — an unpriceable order must read as
    # None so the caller fails closed, never as a value that slides under the cap (finding C1).
    return val if math.isfinite(val) else None


def authorize(order: dict, *, side: str, state: GateState, cfg) -> Decision:
    # ── universal layers (every order) ──────────────────────────────────────────
    if not cfg.enabled:
        return Decision(False, "autopilot disabled (WEBULL_AUTOPILOT_ENABLED not set)", "enable")
    if state.kill_active:
        return Decision(False, "kill-switch active", "kill")
    try:
        safety.validate_order(order)
    except safety.OrderValidationError as e:
        return Decision(False, f"order failed validation: {e}", "validate")
    if str(order.get("side") or "").upper() != side:
        return Decision(False, f"order side {order.get('side')!r} != caller side {side!r}", "validate")
    if not state.in_window:
        return Decision(False, "outside allowed trading window", "window")

    # ── risk-reducing SELLs (protective stops, exits): universal layers only ────
    if side == "SELL":
        return Decision(True, "risk-reducing SELL allowed", "risk-reducing")

    # ── risk-adding BUY entries: full cap stack ─────────────────────────────────
    notional = _notional(order, state.last_price)
    if notional is None:
        return Decision(False, "cannot price order to enforce the cap", "cap")
    if notional > cfg.max_notional:
        return Decision(False, f"${notional:.2f} over ${cfg.max_notional:.2f} per-order cap", "cap")

    sym = str(order.get("symbol") or "").strip().upper()
    if sym in state.open_position_symbols:
        return Decision(False, f"already hold/working {sym} — no averaging up", "positions")
    pos_cap = cfg.max_positions_risk_off if state.spy_risk_off else cfg.max_positions
    if len(state.open_position_symbols) >= pos_cap:
        label = "risk-off " if state.spy_risk_off else ""
        return Decision(False, f"at {label}position cap ({pos_cap})", "positions")

    if state.orders_today >= cfg.max_orders_per_day:
        return Decision(False, f"at daily order cap ({cfg.max_orders_per_day})", "orders_per_day")

    if state.halt_tripped:
        return Decision(False, "daily-loss halt latched for the day", "halt")
    if state.day_pl is None:
        return Decision(False, "day P/L unknown — cannot verify loss halt (fail-closed)", "halt")
    if state.day_pl <= -abs(cfg.daily_loss_halt):
        return Decision(False, f"daily-loss halt (day P/L ${state.day_pl:.2f})", "halt")

    return Decision(True, "entry within all caps", "ok")


def _combo_debit(combo: dict) -> float | None:
    """Worst-case premium of a debit combo in dollars (order-level limit x 100 x quantity).
    None when unpriceable — including NaN/inf, which would defeat the `> cap` comparison
    (finding C1) — so the caller fails closed."""
    try:
        val = float(combo["limit_price"]) * 100.0 * float(combo["quantity"])
    except (TypeError, ValueError, KeyError):
        return None
    return val if math.isfinite(val) else None


def _vertical_width(combo: dict) -> float | None:
    """Strike width of a 2-leg vertical in dollars-per-share, or None if unreadable."""
    legs = combo.get("orders") or []
    if len(legs) != 2:
        return None
    try:
        w = abs(float(legs[0]["strike_price"]) - float(legs[1]["strike_price"]))
    except (TypeError, ValueError, KeyError):
        return None
    return w if math.isfinite(w) and w > 0 else None


def authorize_option(combo: dict, *, state: GateState, cfg, open_option_units: int) -> Decision:
    """The unattended-placement wall for OPTION decisions (2026-08-07 spec). A NEW wall in front
    of the byte-untouched trading.place_option/should_submit — it ADDS strictness, never weakens.

    Admits exactly two shapes, both long/debit and both defined-risk: a SINGLE long option, and a
    debit VERTICAL (2026-08-07, owner-signed order-type widening). In BOTH cases the combo's
    order-level side is BUY only when the net is a DEBIT (safety.build_option_combo derives it
    from the legs), so requiring side==BUY is what structurally excludes every credit/naked
    structure; and the combo limit_price IS the max loss, so cfg.opt_max_debit caps real risk.
    Verticals face two ADDITIONAL walls a single leg cannot: the debit must be under the strike
    width (above it, no outcome profits) and under cfg.opt_max_debit_frac_of_width (an EDGE
    filter — the more of the width you pay, the further the underlying must travel to break even).

    Mirrors authorize()'s layer order: universal walls first, then the risk-adding cap stack (an
    option debit is always risk-adding — there is no SELL bypass here)."""
    if not cfg.enabled:
        return Decision(False, "autopilot disabled (WEBULL_AUTOPILOT_ENABLED not set)", "enable")
    if state.kill_active:
        return Decision(False, "kill-switch active", "kill")
    if not cfg.decisions_enabled:
        return Decision(False, "decision executor disabled (WEBULL_AUTOPILOT_DECISIONS_ENABLED not set)", "enable")
    try:
        safety.validate_option_combo(combo)
    except safety.OrderValidationError as e:
        return Decision(False, f"option combo failed validation: {e}", "validate")
    strategy = combo.get("option_strategy")
    if strategy not in ("SINGLE", "VERTICAL"):
        return Decision(False, f"only SINGLE and debit VERTICAL are executable, got {strategy!r}", "structure")
    if str(combo.get("side") or "").upper() != "BUY":
        # For a vertical, build_option_combo sets side from the net: a credit spread lands on
        # SELL and dies here. This one line is what keeps the sleeve debit-only.
        return Decision(False, "defined-risk debit only: order side must be BUY", "structure")
    if not state.in_window:
        return Decision(False, "outside allowed trading window", "window")

    debit = _combo_debit(combo)
    if debit is None:
        return Decision(False, "cannot price the combo to enforce the debit cap", "cap")
    if debit <= 0:
        # A zero/negative "debit" is not a cheap trade, it is a malformed one — and it would pass
        # every ceiling below by being smaller than all of them.
        return Decision(False, f"debit ${debit:.2f} is not positive — malformed order", "cap")
    if debit > cfg.opt_max_debit:
        return Decision(False, f"debit ${debit:.2f} over ${cfg.opt_max_debit:.2f} option cap", "cap")

    if strategy == "VERTICAL":
        width = _vertical_width(combo)
        if width is None:
            return Decision(False, "cannot read the vertical's strike width", "structure")
        try:
            per_share = float(combo["limit_price"])
        except (TypeError, ValueError, KeyError):
            return Decision(False, "cannot read the vertical's net debit", "structure")
        if per_share >= width:
            return Decision(False, f"net debit {per_share:g} is not below the {width:g} width "
                                   f"(no profitable outcome)", "structure")
        frac = per_share / width
        if frac > cfg.opt_max_debit_frac_of_width:
            return Decision(False, f"debit is {frac:.0%} of the {width:g}-wide spread "
                                   f"(cap {cfg.opt_max_debit_frac_of_width:.0%})", "edge")

    # A long option is pure risk-adding: in a risk-off tape it faces the same freeze the equity
    # entry cap enforces via max_positions_risk_off=0 (final-review finding I4).
    if state.spy_risk_off:
        return Decision(False, "risk-off regime (SPY < 200SMA) — no new option risk", "regime")

    if open_option_units >= cfg.opt_max_open:
        return Decision(False, f"at open-option-units cap ({cfg.opt_max_open})", "positions")
    if state.orders_today >= cfg.max_orders_per_day:
        return Decision(False, f"at daily order cap ({cfg.max_orders_per_day})", "orders_per_day")

    if state.halt_tripped:
        return Decision(False, "daily-loss halt latched for the day", "halt")
    if state.day_pl is None:
        return Decision(False, "day P/L unknown — cannot verify loss halt (fail-closed)", "halt")
    if state.day_pl <= -abs(cfg.daily_loss_halt):
        return Decision(False, f"daily-loss halt (day P/L ${state.day_pl:.2f})", "halt")

    return Decision(True, "option decision within all caps", "ok")
