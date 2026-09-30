"""Autopilot orchestrator: reconcile -> protect -> exits -> entries, PLACING each order only on
the gate's allow. The ONLY new caller of trading.place(confirm=True). Mirrors morning_routine but
places instead of drafting. Order is deliberate: reconcile first (broker truth), risk-reducing
SELLs before risk-adding BUYs (book stays covered even if entries are blocked)."""
from __future__ import annotations

import hashlib
import json
import math
import time
from datetime import date, datetime

from webull_api import (decision_triggers, decisions_exec, discovery, exits, market_data,
                        options_chain, portfolio, push, reconcile, safety, trading)
from webull_api._util import num as _num
from webull_api.journal import pairing
from webull_api.strategy.bars import to_ohlcv
from webull_api.strategy.rsi2 import rsi2_of
from webull_api.swing import screen as swing_screen
from webull_web import journal_ingest, journal_store, position_plans

from . import audit, phone
from . import state as state_mod
from .config import AutopilotConfig
from .gate import GateState, authorize, authorize_option

_QTY_KEYS = ("quantity", "qty", "position", "shares")
_COST_KEYS = ("cost_price", "costPrice", "avgCost", "averageCost", "unitCost", "unit_cost")
_LAST_KEYS = ("last_price", "lastPrice")
_SYMBOL_KEYS = ("symbol", "ticker")


def _default_account(account_id=None, list_accounts=portfolio.list_accounts) -> str:
    if account_id:
        return account_id
    rows = list_accounts()
    rows = rows if isinstance(rows, list) else []
    cash = next((a for a in rows if a.get("account_class") == "INDIVIDUAL_CASH"), None)
    chosen = cash or (rows[0] if rows else None)
    if not chosen:
        raise RuntimeError("no account found")
    return chosen["account_id"]


def _in_window(windows: str, now: datetime) -> bool:
    t = now.time()
    for span in (windows or "").split(","):
        span = span.strip()
        if not span or "-" not in span:
            continue
        a, b = span.split("-", 1)
        try:
            ah, am = (int(x) for x in a.split(":"))
            bh, bm = (int(x) for x in b.split(":"))
        except ValueError:
            continue
        start = ah * 60 + am
        end = bh * 60 + bm
        cur = t.hour * 60 + t.minute
        if start <= cur <= end:
            return True
    return False


def _normalize_rows(raw):
    """(rows, known): known=True ONLY for a recognized shape — a bare list, or a dict envelope with a
    list under data/positions/items. An UNRECOGNIZED shape (a dict with no such list, a 200-with-error
    body, a scalar/None) is UNKNOWN, and the caller MUST fail closed — exactly like a broker read
    exception. A read that normalizes to an empty list is genuinely-flat = known; only an unparseable
    shape is unknown. (Gating real money on a raw shape without this let a dict envelope read as flat,
    silently bypassing the position cap + loss halt — security review 2026-07-09.)"""
    if isinstance(raw, list):
        return raw, True
    if isinstance(raw, dict):
        for k in ("data", "positions", "items"):
            v = raw.get(k)
            if isinstance(v, list):
                return v, True
    return [], False


def _sym(row) -> str:
    for k in _SYMBOL_KEYS:
        v = row.get(k)
        if v:
            return str(v).strip().upper()
    return ""


def _open_symbols(positions, open_orders) -> frozenset:
    out = set()
    for p in (positions if isinstance(positions, list) else []):
        s = _sym(p) if isinstance(p, dict) else ""
        if s:
            out.add(s)
    for o in reconcile._iter_legs(open_orders):
        side = str(o.get("side") or "").strip().upper()
        if side == "BUY":
            s = str(o.get("symbol") or o.get("ticker") or "").strip().upper()
            if s:
                out.add(s)
    return frozenset(out)


_SWING_COST_KEYS = ("cost_price", "costPrice", "avgCost", "averageCost", "unitCost", "unit_cost",
                    "cost")


_RSI2_LEDGER_STALE_DAYS = 7


def _rsi2_real_symbols() -> set:
    """Symbols the REAL RSI2 ledger owns — positive strategy attribution. Read-only.

    STALENESS WINDOW: the ledger is only reconciled while the real RSI2 runner is ENABLED, so a
    disarmed sleeve leaves a frozen file whose last recorded lot would veto every future swing
    exit on that symbol forever. Older than _RSI2_LEDGER_STALE_DAYS -> no attribution.
    An unparseable/missing `updated_at` is NOT treated as stale (a live armed ledger always stamps
    one on every save) — the ambiguous case keeps the veto, which fails toward HOLDING.
    """
    from datetime import date, datetime

    from webull_web import rsi2_real_store
    state = rsi2_real_store.load()
    symbols = {str(l.get("symbol") or "").strip().upper()
               for l in (state.get("owned_lots") or []) if l.get("symbol")}
    if not symbols:
        return symbols
    try:
        stamped = datetime.fromisoformat(str(state.get("updated_at"))).date()
    except (TypeError, ValueError):
        return symbols
    return set() if (date.today() - stamped).days > _RSI2_LEDGER_STALE_DAYS else symbols


def _is_attributed_swing(symbol: str, positions) -> bool:
    """True only when `symbol` is positively attributed as the autopilot's OWN trend/swing lot.

    Two checks, in this order:

    1. **The REAL RSI2 ledger short-circuits everything.** A lot that ledger owns is a
       mean-reversion lot, and a trend break is its SETUP, not its failure — so ledger ownership
       returns False (HOLD) ahead of any plan. Positive attribution outranks a stray swing plan
       for the same symbol, and an unreadable ledger is treated as owning it.
    2. **Otherwise a plan** marked `strategy == "swing"` AND a structural stop that survives
       `reconcile.plan_stop_is_sane` against the position's cost — a stale plan is not
       attribution, it is the 2026-08-13 bug wearing a label.

    Fails CLOSED (returns False -> the caller HOLDS) on every unknown: no plan, no strategy
    field, an unreadable plan store, a missing position, or an unparseable cost. Holding a lot
    that could have been sold costs a missed exit; selling a lot that should have been held
    destroys the trade. The -8%/+20% backstops still protect whatever this holds.
    """
    if not symbol:
        return False
    try:
        if symbol in _rsi2_real_symbols():
            return False
    except Exception:
        return False
    try:
        plan = (position_plans.all_plans() or {}).get(symbol)
    except Exception:
        return False
    if not isinstance(plan, dict) or str(plan.get("strategy") or "").strip().lower() != "swing":
        return False
    cost = None
    for p in (positions if isinstance(positions, list) else []):
        if isinstance(p, dict) and str(p.get("symbol") or "").strip().upper() == symbol:
            cost = _num(p, _SWING_COST_KEYS)
            break
    return reconcile.plan_stop_is_sane(plan.get("structural_stop"), cost)


def _day_pl(positions) -> float | None:
    """Sum of UNREALIZED qty*(last-cost) across open positions (Build 1 — realized-today P/L is
    not tracked; see gate.py's GateState.day_pl comment). Holding NOTHING is an unambiguously
    known $0 (not "unknown") — only a position that EXISTS but can't be priced makes day P/L
    unknowable, and that must fail-closed (a real position may be hiding a large unrealized loss
    the gate can't see). Callers must ALSO treat a failed positions READ (broker error) as unknown
    — this function alone cannot distinguish "no positions" from "couldn't ask" (see run())."""
    rows = positions if isinstance(positions, list) else []
    total, unpriced = 0.0, 0
    for p in rows:
        if not isinstance(p, dict):
            unpriced += 1
            continue
        qty, cost, last = _num(p, _QTY_KEYS), _num(p, _COST_KEYS), _num(p, _LAST_KEYS)
        if qty and cost is not None and last is not None:
            total += qty * (last - cost)
        else:
            unpriced += 1
    return None if unpriced else total


def _realized_today(day, account_id):
    """(realized_pnl, known) — signed realized P/L of TODAY's REAL round-trips for this account,
    from the journal (already refreshed by sync_real at the top of run()). known=False on any read/
    parse error, so the caller MUST fail closed — an undeterminable realized loss must not be read
    as $0. Read-only: journal_store.load_fills + the pure pairing.realized_pnl_on_day."""
    try:
        fills = journal_store.load_fills()
        return pairing.realized_pnl_on_day(fills, day, source="real", account_id=account_id), True
    except Exception:
        return None, False


def _spy_risk_off(get_bars=market_data.get_bars) -> bool:
    """Risk-off when SPY's last close < SMA200. True on ANY error (fail-closed for entries)."""
    try:
        bars = to_ohlcv(get_bars("SPY", "D", count="220"))
        closes = [b["close"] for b in bars if b.get("close") is not None]
        if len(closes) < 200:
            return True
        return closes[-1] < sum(closes[-200:]) / 200
    except Exception:
        return True


def _entry_order(p):
    return safety.build_order(symbol=p.symbol, side="BUY", quantity=str(p.shares),
                              order_type="STOP_LOSS_LIMIT", stop_price=str(p.entry),
                              limit_price=str(round(p.entry * 1.003, 2)), time_in_force="GTC")


# ── DECISIONS stage (2026-08-07 unattended-execution spec) ─────────────────────────────


def _daily_bars(sym: str) -> list:
    """Injectable seam for tests. Errors -> [] (the trigger then fails closed)."""
    try:
        return to_ohlcv(market_data.get_bars(sym, "D", count="3")) or []
    except Exception:
        return []


# Sources whose SELL is a CLOSING sell and must therefore clear a resting protective stop first.
# Keyed on `source` so PROTECT (which only ever fires when nothing rests) and BUY entries are
# untouched — cancelling a stop on the way IN would strip the very protection being placed.
_CLOSING_SELL_SOURCES = ("exit:", "decision:")


_sleep = time.sleep          # seam: tests stub this out
_CLEAR_READ_TRIES = 3        # bounded — never an unbounded hammer on a throttled API
_CLEAR_READ_WAIT_S = 4.0     # order queries are limited 2/2s; 4s spacing stays compliant


def _open_orders_with_retry(acct):
    """trading.get_open_orders with short bounded retries, re-raising the last error.

    A single throttled read (HTTP 429) at the 17:45 run cost the AAPL exit a full day on
    2026-08-18 — the trigger had passed, the clear refused, and no run before the NEXT day's
    close could act. Transient read failures on the exit path deserve more than one attempt;
    after the last one the exception propagates so callers keep their fail-closed paths.
    """
    for i in range(_CLEAR_READ_TRIES):
        try:
            return trading.get_open_orders(acct)
        except Exception:
            if i == _CLEAR_READ_TRIES - 1:
                raise
            _sleep(_CLEAR_READ_WAIT_S)


def _read_with_retry(read, acct):
    """`read(acct)` with the same bounded spacing, retried on a broker throttle ONLY.

    The top-of-run positions / open-orders reads. Every other failure (auth, gateway, shape)
    propagates at once so the caller's fail-closed path runs without a 12 s detour; a 429 is
    the one error that a wait genuinely cures (the positions endpoint allows ~2 reads per
    2–3 s — live probe 2026-09-17).
    """
    for i in range(_CLEAR_READ_TRIES):
        try:
            return read(acct)
        except Exception as e:
            if i == _CLEAR_READ_TRIES - 1 or "TOO_MANY_REQUESTS" not in str(e):
                raise
            _sleep(_CLEAR_READ_WAIT_S)


def _shared_read(name: str, raw, error):
    """A `get_x(account_id)` for PROTECT/EXITS that serves the top-of-run read instead of
    re-reading the broker.

    One run used to read positions THREE times in ~1.2 s (gate state, PROTECT, EXITS) and open
    orders twice; the third read drew 429 on 21 runs since 2026-07-29 and aborted EXITS each
    time. If the shared read failed, the stage must see that failure — raising here lets each
    stage record its own error row, exactly as its own failed read would have — never a
    silently flat book (PROTECT would place no stop and say nothing).
    """
    def get(_account_id):
        if error is not None:
            raise RuntimeError(f"{name} unreadable at top of run ({str(error)[:80]})")
        return raw
    return get


def _clear_resting_stop(acct, symbol: str, *, day: str) -> tuple[bool, str]:
    """Cancel any resting protective SELL stop on `symbol`. Returns (safe_to_place, why).

    A resting stop commits the shares, so a closing SELL is rejected 417 as position-reversing
    (2026-08-13, 2026-08-14). Clearing it is the only way an exit can complete.

    FAILS CLOSED at every step — an unreadable open-orders book, a missing client_order_id, a
    cancel that raises, or a stop still resting on the verification read all return False and the
    caller must NOT place. Placing into a half-cleared state is how an exit becomes an accidental
    short. The verification re-read exists because the broker, not the cancel response, is the
    truth; a 200 on cancel does not prove the order left the book.

    Callers must invoke this only AFTER the gate has authorized, so that a refusal here leaves the
    protective stop exactly where it was rather than stripping protection from a position that was
    never going to be sold.
    """
    try:
        open_orders = _open_orders_with_retry(acct)
    except Exception as e:
        return False, f"open orders unreadable ({str(e)[:60]}) — refusing to clear"
    resting = reconcile.resting_stop_orders(open_orders, symbol)
    if not resting:
        return True, "no resting stop"
    for leg in resting:
        cid = leg.get("client_order_id") or leg.get("clientOrderId")
        if not cid:
            return False, "resting stop carries no client_order_id — cannot cancel"
        try:
            trading.cancel(acct, str(cid))
        except Exception as e:
            return False, f"cancel failed ({str(e)[:60]})"
    try:
        still = reconcile.resting_stop_orders(_open_orders_with_retry(acct), symbol)
    except Exception as e:
        return False, f"post-cancel read failed ({str(e)[:60]}) — cannot prove the book is clear"
    if still:
        return False, f"{len(still)} stop(s) still resting after cancel"
    return True, f"cleared {len(resting)} resting stop(s)"


def _rsi2_now(sym: str, today_iso: str):
    """Fresh RSI(2) for `sym`, or None. Injectable seam for tests.

    Mirrors webull_web/decisions_service._check_rsi2_above (count=30 + same-day bar guard) so the
    watch that ALERTS and this executor that ACTS can never disagree about the same number.

    None on a stale bar, short history, or any broker error — the trigger then fails closed. A
    stale bar must never fire a real SELL: yesterday's RSI(2) says nothing about today's band,
    and this path places real money with no human present.
    """
    try:
        bars = to_ohlcv(market_data.get_bars(sym, "D", count="30")) or []
    except Exception:
        return None
    if not bars or not isinstance(bars[-1], dict):
        return None
    if str(bars[-1].get("date") or bars[-1].get("time") or "")[:10] != today_iso:
        return None
    closes = [b["close"] for b in bars if isinstance(b, dict) and b.get("close") is not None]
    try:
        return rsi2_of(closes)
    except Exception:
        return None


def _prev_close(bars: list, today_iso: str):
    """Close of the last COMPLETED daily bar: bars[-1] unless it IS today's (partial) bar, then
    bars[-2]. Accepts 'date' or 'time' keys. None when undeterminable — fail closed. Pure."""
    rows = [b for b in (bars or []) if isinstance(b, dict) and b.get("close") is not None]
    if not rows:
        return None
    def _day(b):
        return str(b.get("date") or b.get("time") or "")[:10]
    if _day(rows[-1]) == today_iso:
        return float(rows[-2]["close"]) if len(rows) >= 2 else None
    return float(rows[-1]["close"])


def _open_option_units(acted: dict) -> int:
    """HONEST v1 LIMIT: open option units are counted from the executor-OWNED acted-token set
    (round-4 finding I2: the queue file is writer-editable, so counting it let a rewriter bypass
    the units cap) — the toolkit has no broker options-position read yet. A token counts only
    when its submit was attempted AND it carries the |OPTION asset tag; there is no close flow in
    v1, so the cap is a LIFETIME cap. Recovery when a position closes: ask the assistant to
    rewrite the token with a closed: prefix — never hand-edit the trust-root file casually. Pure."""
    return sum(1 for v in acted.values()
               if "|OPTION" in str(v)
               and not any(str(v).startswith(p) for p in ("failed:", "expired:", "deny:")))


def _decision_key(row: dict) -> str:
    """Cooling-map key: id + a hash of EVERY writer-controlled field. A hand-written row that
    reuses a previously-stamped id (re-review finding: the id is writer-controlled, so a bare-id
    key made old stamps donatable) only inherits the old clock when the row is byte-equivalent
    in all writer fields — i.e. it IS the decision the executor already observed. Any altered
    field -> different key -> fresh cooling clock. Pure."""
    material = {k: row.get(k) for k in
                ("id", "ts", "asset", "symbol", "side", "qty", "order_type", "limit_price",
                 "trigger", "expires", "option")}
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return f"{row.get('id')}:{digest}"


def _option_marks(occs: list) -> dict:
    """{occ: {'bid': float|None, 'ask': float|None, 'mid': float|None}} from one live snapshot.
    Any failure -> {} so the caller fails closed (the row stays queued rather than pricing a real
    order off nothing)."""
    from webull_api import options as _options

    out = {}
    try:
        rows = _options.get_option_snapshot(",".join(occs))
    except Exception:
        return {}
    for r in rows or []:
        try:
            bid = float(r["bid"]) if r.get("bid") is not None else None
            ask = float(r["ask"]) if r.get("ask") is not None else None
        except (TypeError, ValueError):
            bid = ask = None
        mid = (bid + ask) / 2 if (bid is not None and ask is not None and ask > 0) else None
        out[r.get("symbol")] = {"bid": bid, "ask": ask, "mid": mid}
    return out


def _vertical_leg_limits(long_mark: dict, net_debit: float) -> tuple | None:
    """Per-leg limits whose NET equals the queued debit ceiling exactly.

    Webull routes a vertical on the combo's net, but build_option_combo derives that net from the
    legs — so the legs must sum to the price the manager actually authorized. Anchor the long leg
    to its live mid and back the short leg out of it: net stays pinned to the ceiling (never
    chases), while both legs sit near the real market so the order is marketable.

    The queued net is a CEILING, so a sub-penny request is FLOORED to the penny tick, never
    rounded up — the executor may pay less than authorized, never more.

    Returns None (fail closed) when the anchor is missing/non-finite, the requested net is below a
    penny, the implied short price is non-positive, or two-decimal rounding would make the ACTUAL
    net differ from the floored authorized one. That last check is not theoretical: a small anchor with a
    sub-penny net rounds both legs to the same price, yielding a $0.00 "debit" spread that is under
    every cap (zero is under the dollar cap, the width, and the fraction-of-width filter) yet is an
    unfillable order that would still consume a lifetime option unit. Pure."""
    anchor = (long_mark or {}).get("mid") or (long_mark or {}).get("ask")
    if anchor is None or not math.isfinite(anchor) or anchor <= 0:
        return None
    if not math.isfinite(net_debit) or net_debit < 0.01:
        return None
    # FLOOR, never round: options trade in penny ticks, and the queued net is a CEILING — rounding
    # a 0.335 request up to 0.34 would pay $0.50/contract more than the manager authorized.
    net = math.floor(net_debit * 100 + 1e-9) / 100
    long_limit = round(anchor, 2)
    short_limit = round(long_limit - net, 2)
    if short_limit <= 0 or long_limit <= 0:
        return None
    if abs((long_limit - short_limit) - net) > 1e-9:
        return None  # rounding moved the net off the authorized price — refuse, never approximate
    return (f"{long_limit:.2f}", f"{short_limit:.2f}")


def _try_place_option(combo: dict, source: str, *, acct, cfg, state, open_option_units,
                      st, day, placed, skipped, errors) -> bool:
    """Options mirror of _try_place: authorize_option -> trading.place_option(confirm=True).
    The ONLY options-placing call site in this module (AST-guarded). Bookkeeping below the
    submit must never relabel a live order as unplaced."""
    d = authorize_option(combo, state=state, cfg=cfg, open_option_units=open_option_units)
    sym = ((combo.get("orders") or [{}])[0].get("symbol")) or "?"
    rec = {"symbol": sym, "side": "BUY", "source": source,
           "allow": d.allow, "reason": d.reason, "layer": d.layer}
    if not d.allow:
        audit.log_decision({**rec, "placed": False}, day=day)
        skipped.append(rec)
        return False
    try:
        result = trading.place_option(acct, combo, confirm=True)
    except Exception as e:
        audit.log_decision({**rec, "placed": False, "error": str(e)[:200]}, day=day)
        errors.append({"symbol": sym, "error": str(e)[:120]})
        return False
    st.orders_today += 1
    st.placed_symbols.append(sym)
    try:
        state_mod.save_state(st)
    except Exception:
        pass
    try:
        audit.log_decision({**rec, "placed": True, "result": result}, day=day)
    except Exception:
        pass
    placed.append({"symbol": sym, "side": "BUY", "source": source})
    return True


def _decisions_stage(*, acct, cfg, now, st, positions, try_place, try_place_option,
                     placed, skipped, errors, protect_actioned, open_orders_known) -> None:
    """Owner-queued standing decisions/directives -> orders through the gate. A row leaves
    'queued' the moment it is ACTED ON (placing/placed/failed/expired); a row that merely isn't
    ready (trigger false, cooling, caps, gate deny) STAYS queued for the next run — every skip
    is audit-logged. Risk-adding BUYs additionally face the cooling veto window (aged from the
    executor's own first_seen stamp) and fail closed when open orders are unreadable.

    AT-MOST-ONCE money path (finding I1): a row is marked 'placing' BEFORE the submit attempt.
    Success flips it to 'placed'; a gate deny flips it back to 'queued' (safe retry); a submit
    ERROR leaves it in 'placing' — never auto-retried, only alerted — because the order may be
    live at the broker. Duplicates are strictly worse than a missed retry here."""
    day = now.date().isoformat()
    if not cfg.decisions_enabled:
        return
    try:
        rows, parse_errors = decisions_exec.load()
    except Exception as e:
        errors.append({"stage": "decisions", "error": str(e)[:120]})
        return
    for err in parse_errors:
        audit.log_decision({"source": "decision:parse", "allow": False, "reason": err,
                            "layer": "parse", "placed": False}, day=day)

    # Ambiguous rows from a prior run: verify at the broker by hand, then mark placed/failed.
    for row in rows:
        if row.get("status") == "placing":
            rec = {"decision_id": row.get("id"), "symbol": row.get("symbol"),
                   "source": "decision:stuck", "allow": False,
                   "reason": "row stuck in 'placing' — verify at the broker, then mark placed/failed by hand",
                   "layer": "stuck", "placed": False}
            audit.log_decision(rec, day=day)
            skipped.append(rec)

    # Executor-owned cooling basis (I2), keyed by id+content-hash and pruned to the rows that
    # still exist — no stale entry survives to donate its stamp to a future forged row.
    fs_map = state_mod.load_first_seen()
    current_keys = {_decision_key(r) for r in rows}
    pruned = {k: v for k, v in fs_map.items() if k in current_keys}
    if pruned.keys() != fs_map.keys():
        fs_map = pruned
        try:
            state_mod.save_first_seen(fs_map)
        except Exception:
            pass

    # Executor-owned rsi2_above confirmations (2026-08-18 two-phase exit spec): the post-close
    # run that can SEE fresh RSI(2) records here; the next core-hours run ACTS on it (evening
    # MARKET submits are 417-rejected). Keyed like cooling so rewrites can't inherit one.
    conf_map = state_mod.load_confirmed()

    # Executor-owned consumed-id tokens (round-3 replay finding): a rewriter can flip a placed
    # row's status back to 'queued' with identical content — the acted set, not the writable
    # queue file, is what says an id's submit was already attempted. Also the source of truth
    # for the decisions/day counter AND the open-option-units cap, so editing the file deflates
    # neither. DELIBERATELY NEVER PRUNED: dropping a token when its queue line disappears would
    # let delete-and-re-add resurrect the id (round-4 review).
    # Token values: "<iso>|<ASSET>" = submit attempted (counts toward caps);
    # "deny:<iso>" = clean gate deny (retry allowed, counts toward nothing);
    # "failed:<iso>" / "expired:<iso>" = terminal without a submit (replay refused, no cap count).
    acted = state_mod.load_acted()
    executed_today = sum(1 for v in acted.values() if str(v).startswith(day))
    open_units = _open_option_units(acted)

    def _save_acted():
        try:
            state_mod.save_acted(acted)
        except Exception:
            pass

    def _consume(rid, value) -> bool:
        """Persist the consumed-id token BEFORE anything irreversible. A failed save must veto
        the submit (round-4 finding I1: a swallowed save failure silently disabled the replay
        wall and the day cap while the order still went out)."""
        acted[rid] = value
        try:
            state_mod.save_acted(acted)
            return True
        except Exception:
            acted.pop(rid, None)
            return False

    def _drop_stamp(key):
        if fs_map.pop(key, None) is not None:
            try:
                state_mod.save_first_seen(fs_map)
            except Exception:
                pass

    pos_qty = {}
    for p in (positions if isinstance(positions, list) else []):
        if isinstance(p, dict):
            s = _sym(p)
            if s:
                pos_qty[s] = _num(p, _QTY_KEYS)

    for row in rows:
        if row.get("status") != "queued":
            continue
        rid = row["id"]
        sym = str(row["symbol"]).strip().upper()
        kind = str((row.get("trigger") or {}).get("kind") or "?")
        src = f"decision:{kind}"

        def _skip(reason, layer):
            rec = {"decision_id": rid, "symbol": sym, "source": src,
                   "allow": False, "reason": reason, "layer": layer, "placed": False}
            audit.log_decision(rec, day=day)
            skipped.append(rec)

        key = _decision_key(row)

        tok = acted.get(rid)
        if tok is not None and not str(tok).startswith("deny:"):
            # The executor already attempted (or terminally resolved) this id; a 'queued' row
            # carrying it is a rewritten/replayed line, not a new decision (round-3 finding).
            # A deny: token is the one non-terminal outcome — retry is legitimate.
            _skip("consumed id re-presented as 'queued' — replay refused; verify at the broker",
                  "replay")
            continue

        if decision_triggers.expired(row, now.date()):
            decisions_exec.mark(rid, "expired", "past expires date")
            acted[rid] = f"expired:{now.isoformat()}"
            _save_acted()
            _drop_stamp(key)
            _skip("expired", "expiry")
            continue

        trig = row.get("trigger") or {}
        last = prev = rsi = None
        if trig.get("kind") in ("green_day", "price_above", "price_below"):
            try:
                last = market_data.spot_price(sym)
            except Exception:
                last = None
            if trig.get("kind") == "green_day":
                prev = _prev_close(_daily_bars(sym), day)
        elif trig.get("kind") == "rsi2_above":
            rsi = _rsi2_now(sym, day)
        if trig.get("kind") == "rsi2_above" and row.get("side") == "SELL":
            # Two-phase exit (2026-08-18 spec): fresh RSI exists only post-close, when MARKET
            # is 417-rejected — so a fresh pass CONFIRMS (stop keeps resting overnight) and a
            # later core-hours run EXECUTES on the confirmation, filling at the next open.
            action, why = decision_triggers.rsi2_phase(
                trig, rsi=rsi, confirmed_on=conf_map.get(key),
                today=now.date(), now_time=now.time())
            if action == "confirm":
                conf_map[key] = day
                try:
                    state_mod.save_confirmed(conf_map)
                except Exception:
                    _skip("cannot persist confirmation — fail closed, re-confirms next run",
                          "state")
                    continue
                decisions_exec.mark(rid, "queued", why)
                _skip(why, "confirm")
                continue
            if action != "execute":
                _skip(why, "trigger")
                continue
            # Consume the single-use confirmation BEFORE anything irreversible; an unsaved
            # consumption must veto the submit (same rule as the acted token).
            conf_map.pop(key, None)
            try:
                state_mod.save_confirmed(conf_map)
            except Exception:
                _skip("cannot persist confirmation consumption — fail closed, row stays queued",
                      "state")
                continue
        else:
            ok, why = decision_triggers.evaluate(trig, last=last, prev_close=prev, rsi=rsi)
            if not ok:
                _skip(why, "trigger")
                continue
        # Risk direction is derived from what the executor will actually BUILD, not from the
        # writer's label: every v1 OPTION row becomes a long debit order, so it is risk-adding
        # regardless of the side field (security review S1). Never trust the label alone.
        risk_adding = row.get("side") == "BUY" or row.get("asset") == "OPTION"

        if risk_adding:
            # Cooling ages from the executor's OWN record, never from anything in the queue file —
            # a writer-supplied first_seen (or backdated ts) is ignored outright (I2 + residuals).
            seen = fs_map.get(key)
            try:
                datetime.fromisoformat(str(seen))
            except (TypeError, ValueError):
                seen = None  # corrupt/unparseable stamp self-heals by restamping (fail-closed)
            if not seen:
                seen = now.isoformat()
                fs_map[key] = seen
                try:
                    state_mod.save_first_seen(fs_map)
                except Exception:
                    pass  # worst case the stamp is lost and cooling restarts later — fail-closed
                decisions_exec.stamp_first_seen(rid, seen)  # informational copy for operators
            row["first_seen"] = seen
        cooling_row = dict(row, side="BUY") if risk_adding else row
        if not decision_triggers.cooled(cooling_row, now, cfg.cooling_minutes):
            _skip(f"cooling: risk-adding row younger than {cfg.cooling_minutes}m (veto window)",
                  "cooling")
            continue
        if risk_adding and not open_orders_known:
            # Mirrors the ENTRIES fail-closed guard: an unreadable open-orders book could hide a
            # resting duplicate (finding I3). Row stays queued.
            _skip("open orders unreadable — BUY decisions fail closed", "open_orders")
            continue
        if executed_today >= cfg.max_decisions_per_day:
            _skip(f"at decisions/day cap ({cfg.max_decisions_per_day})", "decisions_per_day")
            continue

        if row["asset"] == "EQUITY":
            if sym in protect_actioned:
                _skip("already actioned in PROTECT this run", "dedup")
                continue
            qty = row.get("qty")
            held = pos_qty.get(sym)
            # Non-finite broker quantities must not become order fields: NaN slides past
            # validate_order's `<= 0` checks and the SELL path has no notional wall
            # (re-review minor 3, closed at this boundary).
            if held is not None and not math.isfinite(float(held)):
                held = None
            if qty == "ALL":
                if not held:
                    decisions_exec.mark(rid, "failed", "no finite position qty for qty=ALL")
                    acted[rid] = f"failed:{now.isoformat()}"
                    _save_acted()
                    _drop_stamp(key)
                    _skip("no finite position qty for qty=ALL", "position")
                    continue
                qty = held
            elif row["side"] == "SELL":
                # EVERY sell is bounded by the actual holding. gate.authorize waves SELLs past all
                # caps as "risk-reducing" — true only when the quantity comes from a real position
                # (PROTECT/EXITS derive theirs). A writer-chosen qty broke that premise: unbacked
                # or oversized sells reach a real submit, and an oversized one is rejected WHOLE
                # while the row is consumed, so the intended exit silently never happens
                # (security review S2).
                if not held:
                    decisions_exec.mark(rid, "failed", f"no position in {sym} to sell")
                    acted[rid] = f"failed:{now.isoformat()}"
                    _save_acted()
                    _drop_stamp(key)
                    _skip(f"no position in {sym} to sell", "position")
                    continue
                if float(qty) > float(held):
                    decisions_exec.mark(rid, "failed",
                                        f"qty {qty} exceeds the {held} held in {sym}")
                    acted[rid] = f"failed:{now.isoformat()}"
                    _save_acted()
                    _drop_stamp(key)
                    _skip(f"qty {qty} exceeds the {held} held in {sym}", "position")
                    continue
            qty_str = str(int(float(qty))) if float(qty).is_integer() else str(qty)
            try:
                order = safety.build_order(symbol=sym, side=row["side"], quantity=qty_str,
                                           order_type=row["order_type"],
                                           limit_price=row.get("limit_price"),
                                           time_in_force="DAY")
            except Exception as e:
                decisions_exec.mark(rid, "failed", f"order build: {e}")
                acted[rid] = f"failed:{now.isoformat()}"
                _save_acted()
                _drop_stamp(key)
                _skip(f"order build: {e}", "build")
                continue
            if not _consume(rid, f"{now.isoformat()}|EQUITY"):
                _skip("cannot persist consumed-id token — fail closed, row stays queued", "state")
                continue
            decisions_exec.mark(rid, "placing", "submitting")
            errors_before = len(errors)
            try:
                ok = try_place(order, row["side"], src)
            except Exception as e:
                # Can't know whether the raise pre- or post-dated the submit: at-most-once says park.
                _skip(f"placer raised: {str(e)[:80]} — left in 'placing' for human triage", "stuck")
                executed_today += 1
                _drop_stamp(key)
                if row["side"] == "SELL":
                    protect_actioned.add(sym)  # a SELL may be live — block same-run duplicates
                continue
            if ok:
                executed_today += 1
                decisions_exec.mark(rid, "placed")
                _drop_stamp(key)
                if row["side"] == "SELL":
                    protect_actioned.add(sym)
            elif len(errors) > errors_before:
                # Submit raised — the order MAY be live at the broker. Leave 'placing': at-most-once.
                _skip("submit errored after attempt — left in 'placing' for human triage", "stuck")
                executed_today += 1
                _drop_stamp(key)
                if row["side"] == "SELL":
                    protect_actioned.add(sym)
            else:
                # Clean gate deny: nothing was submitted. A deny: token (not a pop) so a crash
                # here can't strand a plain token for a never-submitted row (round-4 minor).
                acted[rid] = f"deny:{now.isoformat()}"
                _save_acted()
                decisions_exec.mark(rid, "queued", "gate denied — will retry")
        else:  # OPTION
            opt = row["option"]
            try:
                exp_date = date.fromisoformat(str(opt["expiry"]))
                occ = options_chain.build_occ(sym, exp_date, opt["right"], float(opt["strike"]))
                qty = str(int(float(opt["quantity"])))
                short_strike = opt.get("short_strike")
                if short_strike is None:
                    # row["side"], NOT a hard-coded "BUY": passing it through makes the gate's
                    # structure wall load-bearing instead of tautological — a SELL-labeled option
                    # row is denied there rather than silently becoming a debit BUY (review S1).
                    leg = safety.build_option_leg(symbol=occ, side=row["side"], quantity=qty,
                                                  limit_price=str(opt["limit_price"]))
                    combo = safety.build_option_combo(strategy="SINGLE", legs=[leg])
                else:
                    short_occ = options_chain.build_occ(sym, exp_date, opt["right"],
                                                        float(short_strike))
                    marks = _option_marks([occ, short_occ])
                    # BOTH legs must quote: the short leg's mark is not used for pricing (its
                    # limit is derived from the net), but its presence is what proves the strike
                    # actually exists and trades. Without this, a typo'd short strike sails past
                    # every wall, gets rejected at the broker, and still burns a lifetime unit.
                    if not marks.get(short_occ):
                        _skip(f"no live market for the short leg {short_occ} — will retry", "marks")
                        continue
                    limits = _vertical_leg_limits(marks.get(occ), float(opt["limit_price"]))
                    if limits is None:
                        _skip("no live mark to anchor the vertical's legs — will retry", "marks")
                        continue
                    long_limit, short_limit = limits
                    legs = [
                        safety.build_option_leg(symbol=occ, side=row["side"], quantity=qty,
                                                limit_price=long_limit),
                        # The short leg is the opposite side BY CONSTRUCTION, never from the row —
                        # a writer cannot turn this into a two-BUY or two-SELL structure.
                        safety.build_option_leg(symbol=short_occ,
                                                side="SELL" if row["side"] == "BUY" else "BUY",
                                                quantity=qty, limit_price=short_limit),
                    ]
                    combo = safety.build_option_combo(strategy="VERTICAL", legs=legs)
            except Exception as e:
                decisions_exec.mark(rid, "failed", f"combo build: {e}")
                acted[rid] = f"failed:{now.isoformat()}"
                _save_acted()
                _drop_stamp(key)
                _skip(f"combo build: {e}", "build")
                continue
            if not _consume(rid, f"{now.isoformat()}|OPTION"):
                _skip("cannot persist consumed-id token — fail closed, row stays queued", "state")
                continue
            decisions_exec.mark(rid, "placing", "submitting")
            errors_before = len(errors)
            try:
                ok = try_place_option(combo, src, open_option_units=open_units)
            except Exception as e:
                _skip(f"placer raised: {str(e)[:80]} — left in 'placing' for human triage", "stuck")
                executed_today += 1
                open_units += 1
                _drop_stamp(key)
                continue
            if ok:
                executed_today += 1
                open_units += 1
                decisions_exec.mark(rid, "placed")
                _drop_stamp(key)
            elif len(errors) > errors_before:
                _skip("submit errored after attempt — left in 'placing' for human triage", "stuck")
                executed_today += 1
                open_units += 1
                _drop_stamp(key)
            else:
                acted[rid] = f"deny:{now.isoformat()}"
                _save_acted()
                decisions_exec.mark(rid, "queued", "gate denied — will retry")


def run(book: float = 400.0, account_id=None, *, cfg=None, now=None) -> dict:
    cfg = cfg or AutopilotConfig.from_env()
    now = now or datetime.now()
    day = now.date().isoformat()
    st = state_mod.load_state(day)
    placed, skipped, errors = [], [], []
    # Symbols placed as entries EARLIER IN THIS SAME RUN. base_state["open_position_symbols"] is
    # a snapshot taken once before the loop, so without this it never learns about same-run
    # placements — with max_positions=1 and several PASSes, every one would place. Union it into
    # each entry's gate state (position-cap + "already hold -> no averaging" checks).
    placed_entry_syms = set()

    try:
        acct = _default_account(account_id)
    except Exception as e:
        return {"placed": [], "skipped": [], "errors": [{"error": str(e)[:120]}],
                "message": "no account"}

    # 1. RECONCILE (sole writer of position state; best-effort journal of fills)
    try:
        journal_ingest.sync_real([acct])
    except Exception:
        pass
    # The run's ONLY positions read and ONLY pre-placement open-orders read. PROTECT and EXITS
    # below consume these same snapshots (see _shared_read) — a per-stage re-read is what drew
    # the 429s. Freshness after a placement is handled where it matters: EXITS dedups against
    # `protect_actioned`, and _clear_resting_stop re-reads open orders (with retry) before any
    # closing SELL.
    raw_positions = raw_open_orders = positions_error = open_orders_error = None
    try:
        # A broker read FAILURE — OR an unrecognized 200 body (a dict envelope, an error object) —
        # is not "flat $0"; it is UNKNOWN, and unknown must fail-closed (an unreadable/unparseable
        # position may be hiding an unrealized loss the gate can't see). Treating either as [] would
        # silently bypass both the position cap and the daily-loss halt for BUYs.
        raw_positions = _read_with_retry(portfolio.get_positions, acct)
        positions, positions_known = _normalize_rows(raw_positions)
    except Exception as e:
        positions, positions_known, positions_error = [], False, e
    try:
        raw_open_orders = _read_with_retry(trading.get_open_orders, acct)
        open_orders, open_orders_known = _normalize_rows(raw_open_orders)
    except Exception as e:
        open_orders, open_orders_known, open_orders_error = [], False, e
    shared_positions = _shared_read("positions", raw_positions, positions_error)
    shared_open_orders = _shared_read("open orders", raw_open_orders, open_orders_error)

    # Fold TODAY's realized loss into the halt's day-P/L. `_day_pl` is UNREALIZED only, so a position
    # stopped out today (an autopilot exit OR a broker-side GTC stop firing while the PC was off)
    # would otherwise vanish from the sum and hide its loss. Clamp realized to min(0, ·): realized
    # GAINS must never offset an unrealized loss (that would LOOSEN the halt — forbidden); only
    # realized losses tighten it. Unknown realized (journal unreadable) -> None -> fail closed,
    # exactly like an unreadable/unpriced positions read.
    unrealized = _day_pl(positions) if positions_known else None
    realized_pnl, realized_known = _realized_today(day, acct)
    day_pl = None if (unrealized is None or not realized_known) else unrealized + min(0.0, realized_pnl)

    base_state = dict(
        kill_active=state_mod.kill_switch_active(cfg.kill_file),
        in_window=_in_window(cfg.windows, now),
        open_position_symbols=_open_symbols(positions, open_orders),
        day_pl=day_pl,
        spy_risk_off=_spy_risk_off(),
    )

    if realized_known:
        st.realized_loss = min(0.0, realized_pnl)
    if day_pl is not None and day_pl <= -abs(cfg.daily_loss_halt):
        st.halt_tripped = True
    if realized_known or st.halt_tripped:
        state_mod.save_state(st)
    base_state["halt_tripped"] = st.halt_tripped

    def _gate_state(last=None, extra_open=frozenset()):
        bs = dict(base_state)
        bs["open_position_symbols"] = base_state["open_position_symbols"] | frozenset(extra_open)
        return GateState(orders_today=st.orders_today, last_price=last, **bs)

    def order_last(order):
        # only entries need a reference price for the cap; MARKET entries would need a snapshot,
        # but the swing entry is STOP_LOSS_LIMIT with a limit_price, so notional prices off that.
        return None

    def _try_place(order, side, source, extra=None, extra_open=frozenset()):
        d = authorize(order, side=side, state=_gate_state(order_last(order), extra_open), cfg=cfg)
        rec = {"symbol": order["symbol"], "side": side, "source": source,
               "allow": d.allow, "reason": d.reason, "layer": d.layer}
        if not d.allow:
            audit.log_decision({**rec, "placed": False}, day=day)
            skipped.append(rec)
            return False
        # Cancel-then-sell. Deliberately AFTER the gate: if the gate had refused, clearing first
        # would have stripped a protective stop off a position that was never going to be sold.
        if side == "SELL" and str(source).startswith(_CLOSING_SELL_SOURCES):
            okc, whyc = _clear_resting_stop(acct, order["symbol"], day=day)
            if not okc:
                blocked = {**rec, "allow": False, "layer": "clear_stop", "reason": whyc}
                audit.log_decision({**blocked, "placed": False}, day=day)
                skipped.append(blocked)
                return False
        try:
            result = trading.place(acct, order, confirm=True)
        except Exception as e:
            audit.log_decision({**rec, "placed": False, "error": str(e)[:200]}, day=day)
            errors.append({"symbol": order["symbol"], "error": str(e)[:120]})
            return False
        # The order is now LIVE at the broker. Everything below is bookkeeping (local counters,
        # disk state, the audit log) and MUST NOT relabel a live order as "not placed" if it
        # fails — a disk/log error here must never cause a real fill to go untracked as "skipped".
        st.orders_today += 1
        st.placed_symbols.append(order["symbol"])
        try:
            state_mod.save_state(st)
        except Exception:
            pass
        try:
            audit.log_decision({**rec, "placed": True, "result": result}, day=day)
        except Exception:
            pass
        placed.append({"symbol": order["symbol"], "side": side, "source": source,
                       "qty": order.get("quantity"),
                       "price": order.get("limit_price") or order.get("stop_price"),
                       **(extra or {})})
        return True

    # 2. PROTECT (risk-reducing SELL stops)
    # Symbols PROTECT actually placed a SELL for THIS RUN. EXITS below consults this: both
    # stages fire on the same condition when the stop is the cost-basis backstop (cost x 0.92,
    # and exits fires "stop" at the same -8%), so without it one run sends two full-size SELLs
    # for one position — a rejection at best, a live short at worst. Declared outside the try
    # so EXITS still sees it if the PROTECT stage raises partway through.
    protect_actioned = set()
    try:
        rows = reconcile.scan_unprotected(acct, plans=position_plans.all_plans(),
                                          stop_loss_pct=8.0,
                                          get_positions=shared_positions,
                                          get_open_orders=shared_open_orders)
        for r in rows:
            if r.error:
                continue
            sym = str(r.symbol or "").strip().upper()
            if r.protective is not None:
                # A resting GTC stop does not sell now — but if EXITS sells the position this
                # run, the stop outlives the position and can later trigger into a short.
                if _try_place(r.protective, "SELL", "protect", extra={"stop": r.stop_price}):
                    protect_actioned.add(sym)
            elif r.synthetic is not None:
                # Fractional: the broker won't rest a stop, so this IS the stop, fired now.
                if _try_place(r.synthetic, "SELL", "protect:synthetic",
                              extra={"stop": r.stop_price, "last": r.last}):
                    protect_actioned.add(sym)
            elif r.skip_reason:
                # Leave a trace of WHY a fractional position went unprotected this run —
                # silence here is exactly the failure mode this change exists to remove.
                rec = {"symbol": r.symbol, "side": "SELL", "source": "protect:synthetic",
                       "allow": False, "reason": r.skip_reason, "layer": "fractional"}
                audit.log_decision({**rec, "placed": False}, day=day)
                skipped.append(rec)
    except Exception as e:
        errors.append({"stage": "protect", "error": str(e)[:120]})

    # 3. EXITS (risk-reducing SELL closes)
    try:
        for r in exits.scan(acct, get_positions=shared_positions):
            sig = r.signal
            if r.error or sig is None or sig.last is None:
                continue
            sym_u = str(sig.symbol or "").strip().upper()
            if sym_u in protect_actioned:
                rec = {"symbol": sig.symbol, "side": "SELL", "source": f"exit:{sig.reason}",
                       "allow": False, "reason": "already actioned in PROTECT this run",
                       "layer": "dedup"}
                audit.log_decision({**rec, "placed": False}, day=day)
                skipped.append(rec)
                continue
            # SLEEVE GUARD (2026-08-15 spec). A trend break is the SETUP for a mean-reversion lot,
            # not its failure — selling on it churns the position out of the trade it was opened
            # for. The paper runner has held this line since 2026-07-24; the real book never got
            # it, and on 2026-08-13/14 this stage tried twice to sell the real RSI2 AAPL share.
            # Only a positively-attributed swing lot may be break-sold. Everything else HOLDS.
            # Backstops ("stop"/"target") are unconditional and never reach this branch.
            # `reasons` defensively: a signal missing/malforming it must not raise here — this
            # stage is wrapped in a broad except, so a crash would silently drop EVERY exit this
            # run, including the unconditional -8% stops. Degrade to the primary reason instead.
            sig_reasons = list(getattr(sig, "reasons", None) or
                               ([sig.reason] if getattr(sig, "reason", None) else []))
            if sig_reasons == ["break"] and not _is_attributed_swing(sym_u, positions):
                rec = {"symbol": sig.symbol, "side": "SELL", "source": f"exit:{sig.reason}",
                       "allow": False, "layer": "sleeve",
                       "reason": "break-only on a lot not attributed as swing — deferring to its "
                                 "own sleeve's exit"}
                audit.log_decision({**rec, "placed": False}, day=day)
                skipped.append(rec)
                continue
            order = safety.build_order(symbol=sig.symbol, side="SELL",
                                       quantity=str(int(sig.qty)) if float(sig.qty).is_integer() else str(sig.qty),
                                       order_type="LIMIT", limit_price=str(round(sig.last, 2)),
                                       time_in_force="DAY")
            _try_place(order, "SELL", f"exit:{sig.reason}")
    except Exception as e:
        errors.append({"stage": "exits", "error": str(e)[:120]})

    # 3.5 DECISIONS (owner-queued standing decisions/directives; 2026-08-07 spec).
    # Positions unreadable -> the whole stage fails closed (qty=ALL and dedup depend on them);
    # rows simply stay queued for the next run.
    if not positions_known:
        audit.log_decision({"source": "decision:stage", "allow": False,
                            "reason": "positions unreadable — decisions fail closed",
                            "layer": "positions", "placed": False}, day=day)
    else:
        try:
            def _tpo(combo, source, *, open_option_units):
                return _try_place_option(combo, source, acct=acct, cfg=cfg,
                                         state=_gate_state(), open_option_units=open_option_units,
                                         st=st, day=day, placed=placed, skipped=skipped,
                                         errors=errors)
            _decisions_stage(acct=acct, cfg=cfg, now=now, st=st, positions=positions,
                             try_place=_try_place, try_place_option=_tpo,
                             placed=placed, skipped=skipped, errors=errors,
                             protect_actioned=protect_actioned,
                             open_orders_known=open_orders_known)
        except Exception as e:
            errors.append({"stage": "decisions", "error": str(e)[:120]})

    # 4. ENTRIES (risk-adding BUY) — only if the window/kill/enable allow at all.
    # Requires a KNOWN open-orders read: the no-averaging + working-order counts (position cap)
    # depend on it, so an unreadable/unparseable open-orders read fails CLOSED for entries — a
    # resting entry could otherwise be invisible and let a duplicate through (security review).
    if not open_orders_known:
        rec = {"symbol": "-", "side": "BUY", "source": "entry", "allow": False,
               "reason": "open orders unreadable — entries fail closed", "layer": "open_orders"}
        audit.log_decision({**rec, "placed": False}, day=day)  # leave an audit trace of WHY entries were suppressed
        skipped.append(rec)
    else:
        try:
            eff_max = min(100.0, round(0.20 * book, 2))
            found = discovery.discover(extra_symbols=[], min_price=10.0, max_price=eff_max, limit=40)
            pass_rows = swing_screen.screen(found.symbols, book) if found.symbols else []
            for r in pass_rows:
                if getattr(r, "error", None) is not None:
                    continue
                p = r.plan
                if getattr(p, "verdict", None) != "PASS":
                    continue
                order = _entry_order(p)
                if _try_place(order, "BUY", "entry",
                              extra={"entry": p.entry, "stop": p.stop, "shares": p.shares},
                              extra_open=frozenset(placed_entry_syms)):
                    placed_entry_syms.add(order["symbol"].upper())
                    position_plans.save_plan({
                        "symbol": p.symbol, "structural_stop": p.stop, "target": p.target,
                        "entry": p.entry, "shares": p.shares, "book": book,
                        "source": "autopilot", "created_at": now.isoformat(),
                    })
        except Exception as e:
            errors.append({"stage": "entries", "error": str(e)[:120]})

    msg = (f"Autopilot: placed {len(placed)} order(s), skipped {len(skipped)}, "
           f"{len(errors)} error(s). Enabled={cfg.enabled} kill={base_state['kill_active']} "
           f"window={base_state['in_window']}.")
    audit_summary = {"placed": len(placed), "skipped": len(skipped), "errors": len(errors)}
    if placed or errors:
        audit.notify(msg, cfg.notify)
        # Owner's phone (2026-09-21): a short note naming what was placed / what errored. Runs
        # after every placement decision is already made and logged; best-effort, never raises.
        note = phone.compose(placed=placed, skipped=skipped, errors=errors, now=now,
                             enabled=cfg.enabled, kill_active=base_state["kill_active"])
        if note and cfg.push_url:
            push.push_text(note["text"], title=note["title"], url=cfg.push_url,
                           priority=note["priority"], tags=note["tags"])
    return {"placed": placed, "skipped": skipped, "errors": errors,
            "summary": audit_summary, "message": msg}
