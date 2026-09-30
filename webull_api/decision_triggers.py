"""Pure decision trigger, cooling, and expiry evaluation.

Decides WHETHER a queued decision may act, never acts itself.
All functions fail-closed (conservative defaults on parse errors).
"""
from datetime import date, datetime, time, timedelta

_CONFIRM_MAX_AGE_DAYS = 3          # spans a weekend (Fri close -> Mon open), bounds staleness
_CORE_START = time(9, 30)
_CORE_END = time(16, 0)


def evaluate(trigger: dict, *, last: float | None, prev_close: float | None,
             rsi: float | None = None) -> tuple[bool, str]:
    """Evaluate if a price trigger condition is met.

    Args:
        trigger: dict with 'kind' and optionally 'level' for price-based triggers.
        last: current price (None = no data).
        prev_close: previous close price (None = no data).
        rsi: fresh RSI(2) for `rsi2_above` (None = no data -> fail closed). Optional so callers
            that never queue that kind are unaffected; the caller computes it, this stays pure.

    Returns:
        (bool, str): decision and human-readable reason.

    Fail-closed: missing inputs or unknown kind -> (False, reason).
    """
    kind = trigger.get("kind")

    if kind == "immediate":
        return (True, "immediate trigger always ready")

    if kind == "green_day":
        if last is None or prev_close is None:
            return (False, f"green_day: missing price data (last={last}, prev_close={prev_close})")
        if last > prev_close:
            return (True, f"green_day: last {last} > prev close {prev_close}")
        else:
            return (False, f"green_day: last {last} <= prev close {prev_close}")

    if kind == "price_above":
        level = trigger.get("level")
        if level is None or last is None:
            return (False, f"price_above: missing level or last (level={level}, last={last})")
        if last > level:
            return (True, f"price_above: last {last} > level {level}")
        else:
            return (False, f"price_above: last {last} <= level {level}")

    if kind == "price_below":
        level = trigger.get("level")
        if level is None or last is None:
            return (False, f"price_below: missing level or last (level={level}, last={last})")
        if last < level:
            return (True, f"price_below: last {last} < level {level}")
        else:
            return (False, f"price_below: last {last} >= level {level}")

    if kind == "rsi2_above":
        # The mean-reversion sleeve's own exit band. Mirrors the watch layer
        # (webull_web/decisions_service._check_rsi2_above) so the alert that fires and the
        # executor that ACTS can never disagree about the same number.
        try:
            level = float(trigger.get("threshold", 70.0))
        except (TypeError, ValueError):
            return (False, f"rsi2_above: unparseable threshold {trigger.get('threshold')!r}")
        if rsi is None:
            return (False, "rsi2_above: no fresh RSI(2) — failing closed")
        if rsi > level:
            return (True, f"rsi2_above: RSI(2) {rsi:.1f} > {level:g}")
        return (False, f"rsi2_above: RSI(2) {rsi:.1f} <= {level:g}")

    # Unknown kind
    return (False, f"unknown trigger kind: {kind}")


def rsi2_phase(trigger: dict, *, rsi: float | None, confirmed_on: str | None,
               today: date, now_time: time) -> tuple[str, str]:
    """Two-phase rsi2_above SELL decision: ("confirm"|"execute"|"deny", reason). Pure.

    Fresh RSI(2) exists only post-close — exactly when the broker refuses MARKET orders
    (417 "only limit orders in extended hours", live 2026-08-18). So a fresh-RSI pass only
    CONFIRMS (recorded by the caller in executor-owned state; the protective stop keeps
    resting overnight), and a later core-hours run EXECUTES on a prior-day confirmation —
    backtest-faithful (signal on close, fill at next open). Fail-closed on every parse error.
    """
    try:
        level = float(trigger.get("threshold", 70.0))
    except (TypeError, ValueError):
        return ("deny", f"rsi2_above: unparseable threshold {trigger.get('threshold')!r}")
    if rsi is not None:
        if rsi > level:
            return ("confirm", f"rsi2_above: RSI(2) {rsi:.1f} > {level:g} on today's close — "
                               "confirmed; executes at the next open")
        return ("deny", f"rsi2_above: RSI(2) {rsi:.1f} <= {level:g}")
    if not confirmed_on:
        return ("deny", "rsi2_above: no fresh RSI(2) and no standing confirmation — failing closed")
    try:
        conf = date.fromisoformat(str(confirmed_on))
    except (TypeError, ValueError):
        return ("deny", f"rsi2_above: unparseable confirmation date {confirmed_on!r} — failing closed")
    if conf >= today:
        return ("deny", f"rsi2_above: confirmation {conf} is not from a prior session — "
                        "executes at the next open, never the same evening")
    if (today - conf).days > _CONFIRM_MAX_AGE_DAYS:
        return ("deny", f"rsi2_above: confirmation {conf} is stale (>{_CONFIRM_MAX_AGE_DAYS}d) — "
                        "failing closed; the next post-close run must re-confirm")
    if not (_CORE_START <= now_time < _CORE_END):
        return ("deny", "rsi2_above: confirmed but outside core hours — MARKET needs the open")
    return ("execute", f"rsi2_above: confirmed on the {conf} close — executing at the open")


def cooled(row: dict, now: datetime, cooling_minutes: int) -> bool:
    """Check if a BUY decision has cooled sufficiently; SELL rows always pass.

    BUYs age from `first_seen` when present (on the autopilot path the executor ALWAYS sets it
    from its own tamper-proof map before calling — the `ts` fallback below only serves other
    callers/tests), so a queue writer who backdates `ts` gains nothing (final-review finding I2).

    Fail-closed: unparseable/missing basis timestamp or unknown side -> False.
    """
    side = row.get("side")

    # SELL rows always pass cooling check
    if side == "SELL":
        return True

    # BUY rows require cooling period
    if side == "BUY":
        basis = row.get("first_seen") or row.get("ts")
        if not basis:
            return False  # Fail-closed: no basis timestamp

        try:
            ts = datetime.fromisoformat(basis)
            elapsed = now - ts
            required = timedelta(minutes=cooling_minutes)
            return elapsed >= required
        except (ValueError, TypeError):
            return False  # Fail-closed: unparseable timestamp

    # Unknown side
    return False  # Fail-closed


def expired(row: dict, today: date) -> bool:
    """Check if a decision has expired.

    Args:
        row: decision row with 'expires' (ISO date string, e.g., "2026-08-07").
        today: current date.

    Returns:
        bool: True if today > expires date; True on parse error (fail-closed).

    Fail-closed: unparseable expires or missing -> True (junk never lives forever).
    """
    expires_str = row.get("expires")
    if not expires_str:
        return True  # Fail-closed: missing expires

    try:
        expires = date.fromisoformat(expires_str)
        return today > expires
    except (ValueError, TypeError):
        return True  # Fail-closed: unparseable expires
