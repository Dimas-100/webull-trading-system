"""FastMCP server: the order-intent channel.

draft_order -> a validated, previewed pending intent in data/intents/ (JSON only -- nothing renders it
since the web app was archived 2026-09-28; the owner places it via trade-placer / exit-placer with the
codeword, or by hand in the Webull app).
place_order (Task 5) -> the one quarantined direct real-order surface (codeword + cap gated).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from mcp.server.fastmcp import FastMCP

from webull_api import discovery, exits, market_data, portfolio, reconcile, safety, trading, watchlist
from webull_api.autopilot import run as autopilot_run_impl
from webull_api.market_data import MarketDataNotEntitledError
from webull_api.safety import OrderValidationError
from webull_api.swing import screen as swing_screen
from webull_web import intent_store, position_plans
from webull_mcp.safe import safe as _shared_safe

mcp = FastMCP("webull-trade")

_INTENT_TTL_MIN = 30
_ROUTINE_TTL_MIN = int(os.environ.get("WEBULL_ROUTINE_TTL_MIN", "240"))  # scheduled-routine drafts (4h)


def _safe(fn) -> str:
    # OrderValidationError is tried BEFORE the catch-all so it keeps its specific payload.
    return _shared_safe(fn, handlers=[
        (OrderValidationError, lambda e: {"error": "OrderValidationError", "message": str(e)[:300]}),
        (MarketDataNotEntitledError, lambda e: {"error": "MarketDataNotEntitled", "message": "Market data not subscribed."}),
    ])


def _now():
    return datetime.now(timezone.utc)


def _default_account(account_id):
    if account_id:
        return account_id
    accts = portfolio.list_accounts()
    rows = accts if isinstance(accts, list) else []
    cash = next((a for a in rows if a.get("account_class") == "INDIVIDUAL_CASH"), None)
    chosen = cash or (rows[0] if rows else None)
    if not chosen:
        raise RuntimeError("no account found")
    return chosen["account_id"]


# ── draft_order (Phase 1) ─────────────────────────────────────────────────────

def draft(symbol, side, quantity, order_type="LIMIT", limit_price=None, stop_price=None,
          time_in_force="DAY", thesis="", account_id=None) -> str:
    def go():
        acct = _default_account(account_id)
        order = safety.build_order(symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                                   limit_price=limit_price, stop_price=stop_price, time_in_force=time_in_force)
        safety.validate_order(order)
        preview = trading.preview(acct, order)
        now = _now()
        intent = intent_store.save_intent({
            "source": "desktop", "account_id": account_id,
            "symbol": order["symbol"], "side": order["side"], "order_type": order["order_type"],
            "quantity": order["quantity"], "limit_price": order.get("limit_price"),
            "stop_price": order.get("stop_price"), "time_in_force": order["time_in_force"],
            "thesis": thesis, "preview": preview, "status": "pending",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=_INTENT_TTL_MIN)).isoformat(),
        })
        return {"drafted": intent, "preview": preview,
                "message": ("Drafted to data/intents/ (nothing renders it). The owner places it: "
                            "trade-placer / exit-placer (codeword) or by hand in the Webull app.")}

    return _safe(go)


@mcp.tool()
def draft_order(symbol: str, side: str, quantity: str, order_type: str = "LIMIT",
                limit_price: str | None = None, stop_price: str | None = None,
                time_in_force: str = "DAY", thesis: str = "", account_id: str | None = None) -> str:
    """Draft a US equity/ETF order from chat as a pending intent (does NOT place it). side BUY/SELL;
    order_type LIMIT/MARKET/STOP_LOSS/STOP_LOSS_LIMIT; time_in_force DAY/GTC (use GTC after hours).
    Add a short `thesis` (why). It validates + previews (est. cost) and writes the pending intent to
    data/intents/ -- nothing renders it; the owner places it (trade-placer / exit-placer with the
    codeword, or by hand in the Webull app)."""
    return draft(symbol, side, quantity, order_type, limit_price, stop_price, time_in_force, thesis, account_id)


# ── place_order (Phase 2): the ONE quarantined direct real-order surface ──────

def _last_price(symbol):
    snap = market_data.get_snapshot(symbol)
    rows = snap.get("data") if isinstance(snap, dict) else snap
    for r in (rows or []):
        if isinstance(r, dict):
            for k in ("price", "close", "last", "lastPrice"):
                v = r.get(k)
                if v is not None:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        pass
    return None


def _notional(order, last):
    try:
        qty = float(order["quantity"])
    except (TypeError, ValueError):
        return None
    px = float(order["limit_price"]) if order.get("limit_price") is not None else last
    return qty * px if px is not None else None


def _journal_real_fills(account_ids) -> int:
    """Best-effort: reconcile real fills into the learning-loop Journal AFTER a successful submit, so a
    trade placed from chat is written down with no separate journaling step. Read-only capture (reads
    order history; appends to the journal) — it runs only after the codeword + cap gate has passed and
    can never affect the placement. A just-submitted, not-yet-FILLED order journals 0 (reconciled later)."""
    try:
        from webull_web import journal_ingest
        n, _err = journal_ingest.sync_real(list(account_ids))
        return n
    except Exception:
        return 0


def place(symbol, side, quantity, codeword, order_type="LIMIT", limit_price=None,
          stop_price=None, time_in_force="DAY", account_id=None) -> str:
    def go():
        secret = os.environ.get("WEBULL_TRADE_CODEWORD")
        if not secret:
            return {"error": "DirectTradingDisabled",
                    "message": "Set WEBULL_TRADE_CODEWORD in .env to enable direct placing."}
        if codeword != secret:
            return {"error": "BadCodeword", "message": "Confirmation codeword did not match."}
        order = safety.build_order(symbol=symbol, side=side, quantity=quantity, order_type=order_type,
                                   limit_price=limit_price, stop_price=stop_price, time_in_force=time_in_force)
        safety.validate_order(order)
        cap = float(os.environ.get("WEBULL_TRADE_MAX_NOTIONAL", "500"))
        last = _last_price(order["symbol"]) if order["order_type"] == "MARKET" else None
        notional = _notional(order, last)
        if notional is None:
            return {"error": "CapUnverifiable",
                    "message": ("Could not price the order to enforce the cap — use a LIMIT order or draft_order. "
                                "Not for a protective stop: it stays a stop-market (the armed autopilot's "
                                "PROTECT rests it, else place it by hand in the Webull app — never as a "
                                "stop-limit or LIMIT).")}
        if notional > cap:
            return {"error": "OverCap",
                    "message": f"${notional:.2f} exceeds the ${cap:.2f} per-order cap — use draft_order."}
        acct = _default_account(account_id)
        result = trading.place(acct, order, confirm=True)
        journaled = _journal_real_fills([acct])
        return {"placed": True, "notional": notional, "result": result, "journaled": journaled}

    return _safe(go)


@mcp.tool()
def place_order(symbol: str, side: str, quantity: str, codeword: str, order_type: str = "LIMIT",
                limit_price: str | None = None, stop_price: str | None = None,
                time_in_force: str = "DAY", account_id: str | None = None) -> str:
    """DIRECTLY place a REAL US equity/ETF order from chat — only for small,
    already-decided trades. Requires the user's secret `codeword` (the user must type it; you do NOT
    know it — never guess or invent it) and the order must be within the per-order $ cap (else use
    draft_order). side BUY/SELL; time_in_force DAY/GTC (GTC after hours). Submits a LIVE order — use
    only when the user explicitly says to place it now AND supplies the codeword."""
    return place(symbol, side, quantity, codeword, order_type, limit_price, stop_price, time_in_force, account_id)


# ── screen_and_draft: swing screen -> auto-draft each PASS as a GTC buy-stop intent ──

def _universe(watchlist_arg, symbols_arg):
    if symbols_arg:
        return [s.strip().upper() for s in symbols_arg.split(",") if s.strip()][:40]
    lists = watchlist.get_watchlists()
    key = (watchlist_arg or "My Watchlist").strip()
    target = (next((w for w in lists if str(w.get("id")) == key), None)
              or next((w for w in lists if (w.get("name") or "").strip().lower() == key.lower()), None))
    if target is None:
        names = ", ".join(w.get("name") or "?" for w in lists) or "(none)"
        return {"error": "WatchlistNotFound", "message": f"No watchlist matched {key!r}. Available: {names}"}
    return [r["symbol"].strip().upper() for r in watchlist.get_watchlist_symbols(target["id"]) if r.get("symbol")][:40]


def screen_draft(watchlist="", symbols="", book=500.0, ttl_min=_INTENT_TTL_MIN) -> str:
    def go():
        universe = _universe(watchlist, symbols)
        if isinstance(universe, dict):       # WatchlistNotFound payload
            return universe
        if not universe:
            return {"drafted": [], "skipped": [], "errors": [], "book": book, "message": "No symbols to screen."}
        pending = {(i.get("symbol") or "").upper() for i in intent_store.list_intents(_now().isoformat())}
        drafted, skipped, errors = [], [], []
        acct = None
        for r in swing_screen.screen(universe, book):
            if r.error is not None:
                errors.append({"symbol": r.symbol, "error": r.error})
                continue
            p = r.plan
            if p.verdict != "PASS":
                skipped.append({"symbol": r.symbol, "reason": p.reason})
                continue
            if r.symbol.upper() in pending:
                skipped.append({"symbol": r.symbol, "reason": "already drafted (pending)"})
                continue
            order = safety.build_order(symbol=p.symbol, side="BUY", quantity=str(p.shares),
                                       order_type="STOP_LOSS_LIMIT", stop_price=str(p.entry),
                                       limit_price=str(round(p.entry * 1.003, 2)), time_in_force="GTC")
            safety.validate_order(order)
            if acct is None:
                acct = _default_account(None)
            preview = trading.preview(acct, order)
            now = _now()
            thesis = (f"Swing PASS: R:R {p.rr}, risk {p.actual_risk_pct}%, exit {p.exit_mode}. "
                      "Manual checks (not leveraged / no binary event) assumed — verify before confirming.")
            intent_store.save_intent({
                "source": "swing_screen", "account_id": None,
                "symbol": order["symbol"], "side": order["side"], "order_type": order["order_type"],
                "quantity": order["quantity"], "limit_price": order.get("limit_price"),
                "stop_price": order.get("stop_price"), "time_in_force": order["time_in_force"],
                "thesis": thesis, "preview": preview, "status": "pending",
                "created_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=ttl_min)).isoformat(),
            })
            position_plans.save_plan({
                "symbol": p.symbol, "structural_stop": p.stop, "target": p.target,
                "entry": p.entry, "shares": p.shares, "book": book,
                "source": "swing_screen", "created_at": _now().isoformat(),
            })
            drafted.append({"symbol": p.symbol, "entry": p.entry, "stop": p.stop, "shares": p.shares, "rr": p.rr})
        msg = (f"Drafted {len(drafted)} buy-stop(s) to data/intents/ (nothing renders them). The owner "
               "places each: trade-placer (codeword) or by hand in the Webull app.")
        return {"drafted": drafted, "skipped": skipped, "errors": errors, "book": book, "message": msg}

    return _safe(go)


@mcp.tool()
def screen_and_draft(watchlist: str = "", symbols: str = "", book: float = 500.0) -> str:
    """Run the user's swing screen over a watchlist (or an explicit comma-separated `symbols` list)
    and DRAFT every PASS as its buy-stop-limit entry (does NOT place — the owner places it: trade-placer
    with the codeword, or by hand in the Webull app). `watchlist` = id or name (default "My Watchlist");
    `symbols` overrides it.
    `book` = account size for sizing (default 500). Skips symbols already drafted (pending).
    Returns {drafted, skipped, errors}."""
    return screen_draft(watchlist, symbols, book)


def discover_draft(book: float = 500.0, min_price: float = 10.0, max_price: float = 100.0,
                   extra_symbols: str = "", limit: int = 40, ttl_min: int = _INTENT_TTL_MIN) -> str:
    """Discovery -> swing screen -> draft every PASS. The shared core behind the `discover_and_draft`
    tool AND the scheduled `morning_routine` (so both source candidates by SCANNING, not from a
    watchlist). Draft-only; never places. `ttl_min` lets the unattended routine use a longer TTL."""
    def go():
        extras = [s.strip() for s in extra_symbols.split(",") if s.strip()]
        # Align discovery's ceiling with the planner's dynamic ceiling (0.20 * book) so the two agree
        # and auto-scale as the account grows; max_price stays an optional harder cap.
        eff_max = min(max_price, round(0.20 * book, 2))
        found = discovery.discover(extra_symbols=extras, min_price=min_price,
                                   max_price=eff_max, limit=limit)
        if not found.symbols:
            return {"discovered": [], "scanned": found.scanned, "in_band": found.in_band,
                    "drafted": [], "skipped": [], "errors": [],
                    "message": (f"Scanned {found.scanned} names; none in the "
                                f"${min_price:.0f}-${eff_max:.0f} band with a price.")}
        # Reuse the tested screen->draft path (never places; validates + previews only).
        res = json.loads(screen_draft(symbols=",".join(found.symbols), book=book, ttl_min=ttl_min))
        if "error" in res:
            return res
        res.update(discovered=found.symbols, scanned=found.scanned, in_band=found.in_band)
        res["message"] = (f"Discovered {len(found.symbols)} in-band candidate(s) from "
                          f"{found.scanned} scanned. " + res.get("message", ""))
        return res

    return _safe(go)


@mcp.tool()
def discover_and_draft(book: float = 500.0, min_price: float = 10.0, max_price: float = 100.0,
                       extra_symbols: str = "", limit: int = 40) -> str:
    """Claude sources its OWN swing candidates — NOT the user's watchlist. Scans the curated liquid
    universe (webull_api.discovery.CURATED_UNIVERSE, ~180 US stocks + broad ETFs) plus any injected
    `extra_symbols` (comma-separated — e.g. market movers pulled at the agent layer), filters to the
    price band via one batch snapshot, runs the user's swing screen, and DRAFTS every PASS as its
    buy-stop-limit entry. Does NOT place — the owner places it (trade-placer with the codeword, or by
    hand in the Webull app). Real liquidity is enforced by the swing planner's gate, not here.

    The effective upper band tracks the swing planner's own price ceiling — **20% of `book`** (its
    per-position concentration cap) — so discovery never wastes a screen on a name the planner would
    reject on price. `max_price` is an optional hard cap on top of that. `book` = account size for
    sizing; `limit` caps how many in-band names get the deep screen (core-first).
    Returns {discovered, scanned, in_band, drafted, skipped, errors}."""
    return discover_draft(book, min_price, max_price, extra_symbols, limit)


# ── manage_exits: scan open positions -> draft a full-close SELL per exit signal ──

def _qty_str(qty) -> str:
    f = float(qty)
    return str(int(f)) if f.is_integer() else str(f)


def exits_draft(account_id="", stop_loss_pct=8.0, take_profit_pct=20.0, ttl_min=_INTENT_TTL_MIN) -> str:
    def go():
        acct = _default_account(account_id or None)
        cfg = exits.ExitConfig(stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct)
        rows = exits.scan(acct, cfg=cfg)
        now = _now()
        now_iso = now.isoformat()
        pending = {(i.get("symbol") or "").upper()
                   for i in intent_store.list_intents(now_iso) if i.get("side") == "SELL"}
        drafted, held, errors = [], [], []
        for r in rows:
            if r.error is not None:
                errors.append({"symbol": r.symbol, "error": r.error})
                continue
            sig = r.signal
            if sig is None:
                held.append({"symbol": r.symbol, "reason": r.held})
                continue
            if r.symbol.upper() in pending:
                held.append({"symbol": r.symbol, "reason": "already drafted (pending)"})
                continue
            if sig.last is None:
                held.append({"symbol": r.symbol, "reason": "no price"})
                continue
            order = safety.build_order(symbol=sig.symbol, side="SELL", quantity=_qty_str(sig.qty),
                                       order_type="LIMIT", limit_price=str(round(sig.last, 2)),
                                       time_in_force="DAY")
            safety.validate_order(order)
            preview = trading.preview(acct, order)
            intent_store.save_intent({
                "source": "exit_manager", "account_id": None,
                "symbol": order["symbol"], "side": order["side"], "order_type": order["order_type"],
                "quantity": order["quantity"], "limit_price": order.get("limit_price"),
                "stop_price": None, "time_in_force": order["time_in_force"],
                "thesis": f"Exit ({sig.reason}): {sig.detail}", "preview": preview, "status": "pending",
                "created_at": now_iso,
                "expires_at": (now + timedelta(minutes=ttl_min)).isoformat(),
            })
            drafted.append({"symbol": sig.symbol, "qty": _qty_str(sig.qty), "reason": sig.reason,
                            "last": sig.last, "unrealized_pct": sig.unrealized_pct})
        msg = (f"Drafted {len(drafted)} SELL(s) to data/intents/ (nothing renders them). The owner "
               "places each: exit-placer (codeword) or by hand in the Webull app.")
        return {"drafted": drafted, "held": held, "errors": errors, "message": msg}

    return _safe(go)


@mcp.tool()
def manage_exits(account_id: str = "", stop_loss_pct: float = 8.0, take_profit_pct: float = 20.0) -> str:
    """Scan your open positions and DRAFT a full-close SELL (LIMIT @ last, DAY) for each one that
    trips an exit signal — a cost-basis stop (default -8%), a profit target (default +20%), or a
    daily close below its 20-day average (does NOT place — the owner places it: exit-placer with the
    codeword, or by hand in the Webull app). Optional `stop_loss_pct` / `take_profit_pct` override the
    thresholds. Skips option positions and symbols already drafted (pending). Returns
    {drafted, held, errors}."""
    return exits_draft(account_id, stop_loss_pct, take_profit_pct)


# ── protect_positions: draft a resting GTC protective stop for any unprotected open position ──

def protect(account_id="", stop_loss_pct=8.0, ttl_min=_INTENT_TTL_MIN) -> str:
    def go():
        acct = _default_account(account_id or None)
        plans = position_plans.all_plans()
        rows = reconcile.scan_unprotected(acct, plans=plans, stop_loss_pct=stop_loss_pct)
        now = _now()
        now_iso = now.isoformat()
        pending = {(i.get("symbol") or "").upper()
                   for i in intent_store.list_intents(now_iso) if i.get("side") == "SELL"}
        drafted, needs_manual, protected, errors = [], [], [], []
        for r in rows:
            if r.error is not None:
                errors.append({"symbol": r.symbol, "error": r.error})
                continue
            if r.protective is None:
                # A fractional row's skip_reason names why it went unprotected THIS run
                # (Webull won't rest a stop on a fractional qty); the generic wording is
                # only right for a whole-share row with no structural stop or cost basis.
                reason = (r.skip_reason if r.fractional and r.skip_reason
                         else "no structural stop or cost basis to price a protective stop")
                needs_manual.append({"symbol": r.symbol, "reason": reason})
                continue
            if r.symbol.upper() in pending:
                protected.append({"symbol": r.symbol, "reason": "already drafted (pending)"})
                continue
            try:
                order = r.protective
                safety.validate_order(order)
                preview = trading.preview(acct, order)
                intent_store.save_intent({
                    "source": "position_protect", "account_id": None,
                    "symbol": order["symbol"], "side": order["side"], "order_type": order["order_type"],
                    "quantity": order["quantity"], "limit_price": order.get("limit_price"),
                    "stop_price": order.get("stop_price"), "time_in_force": order["time_in_force"],
                    "thesis": f"Protective stop ({r.stop_source}) at {r.stop_price} — resting GTC. "
                              "Placed, never lowered.",
                    "preview": preview, "status": "pending",
                    "created_at": now_iso,
                    "expires_at": (now + timedelta(minutes=ttl_min)).isoformat(),
                })
                drafted.append({"symbol": r.symbol, "stop": r.stop_price, "source": r.stop_source,
                                "qty": _qty_str(r.qty)})
            except Exception as e:
                errors.append({"symbol": r.symbol, "error": str(e)[:120]})
        msg = (f"Drafted {len(drafted)} protective stop(s) to data/intents/ (nothing renders them). The "
               "armed autopilot's PROTECT rests these, else the owner places the stop-market by hand in "
               f"the Webull app (never a stop-limit). {len(needs_manual)} need a manual stop.")
        return {"drafted": drafted, "needs_manual": needs_manual, "protected": protected,
                "errors": errors, "message": msg}

    return _safe(go)


@mcp.tool()
def protect_positions(account_id: str = "", stop_loss_pct: float = 8.0) -> str:
    """Draft a resting GTC protective stop (SELL STOP_LOSS, market-on-trigger) for every open long-equity
    position that has NO resting stop at the broker — so downside is covered while your PC is off (charter
    §5); market-on-trigger so the stop is guaranteed to fill on a gap-down.
    Uses each position's plan-of-record structural stop when known, else a cost-basis backstop (default
    -8%). Does NOT place — the armed autopilot's PROTECT stage rests these stops; otherwise the owner
    places the stop-market by hand in the Webull app. Never via the codeword place_order (it refuses an
    unpriced STOP_LOSS) and never converted to a stop-limit or LIMIT to pass its cap check (a stop-limit
    can fail to fill on a gap-down; a marketable LIMIT SELL closes the position instead of protecting it).
    Skips positions already protected (or already drafted) and option positions. Returns
    {drafted, needs_manual, protected, errors}."""
    return protect(account_id, stop_loss_pct)


# ── annotate_intent: write a red-team bear case + verdict onto a pending draft (advisory) ──

_RED_TEAM_VERDICTS = {"kill", "caution", "proceed"}


def annotate_pending(symbol, verdict, bear_case, side="BUY") -> str:
    def go():
        v = (verdict or "").strip().lower()
        if v not in _RED_TEAM_VERDICTS:
            return {"error": "BadVerdict",
                    "message": f"verdict must be one of {sorted(_RED_TEAM_VERDICTS)}, got {verdict!r}"}
        now_iso = _now().isoformat()
        updated = intent_store.annotate(symbol, side, now_iso,
                                        bear_case=bear_case, red_team_verdict=v, red_team_at=now_iso)
        if updated is None:
            return {"error": "IntentNotFound",
                    "message": f"No pending {side} draft for {symbol!r} to annotate."}
        return {"annotated": updated}

    return _safe(go)


@mcp.tool()
def annotate_intent(symbol: str, verdict: str, bear_case: str, side: str = "BUY") -> str:
    """Write a red-team BEAR CASE + verdict onto a pending Desktop draft in data/intents/ (JSON; no
    display surface). `verdict` = kill | caution | proceed. Finds the most-recent pending intent for
    `symbol` + `side` (default BUY, the entry). ADVISORY ONLY — it does NOT place, dismiss, or change
    the order; the user still decides. Returns {annotated} or {error: BadVerdict|IntentNotFound}."""
    return annotate_pending(symbol, verdict, bear_case, side)


# ── morning_routine: one unattended call -> screen entries + scan exits, drafted with a longer TTL ──

def morning_routine_run(book=500.0, account_id="", symbols="", extra_symbols="") -> str:
    def go():
        # Claude-managed: DISCOVER candidates by scanning the curated universe by default (NOT the
        # user's watchlist). `symbols` is an explicit override; `extra_symbols` injects extra names
        # (e.g. movers) into discovery — empty on an unattended run, since the SDK has no movers feed.
        if symbols:
            entries = json.loads(screen_draft(symbols=symbols, book=book, ttl_min=_ROUTINE_TTL_MIN))
        else:
            entries = json.loads(discover_draft(book=book, extra_symbols=extra_symbols,
                                                ttl_min=_ROUTINE_TTL_MIN))
        exits_res = json.loads(exits_draft(account_id, ttl_min=_ROUTINE_TTL_MIN))
        protection = json.loads(protect(account_id, ttl_min=_ROUTINE_TTL_MIN))
        n_entry = len(entries.get("drafted", []))
        n_exit = len(exits_res.get("drafted", []))
        n_prot = len(protection.get("drafted", []))
        return {
            "entries": entries, "exits": exits_res, "protection": protection,
            "message": (f"Morning routine: {n_entry} entry draft(s) + {n_exit} exit draft(s) + "
                        f"{n_prot} protective stop(s) saved to data/intents/ (nothing renders them). "
                        "Entries/exits: trade-placer / exit-placer (codeword) or the Webull app. "
                        "Stops: the armed autopilot's PROTECT or by hand (stop-market, never a stop-limit)."),
        }

    return _safe(go)


@mcp.tool()
def morning_routine(book: float = 500.0, account_id: str = "", symbols: str = "",
                    extra_symbols: str = "") -> str:
    """Run the full pre-market routine in ONE call: DISCOVER swing entries by SCANNING the curated
    universe (NOT a watchlist — this account is Claude-managed) AND scan open positions for exits AND
    protect open positions with resting stops, drafting every actionable order to data/intents/ with a
    longer TTL (so morning drafts survive until you review — JSON only, no display surface). `symbols`
    (comma-separated) is an explicit override that screens those exact names instead of discovering;
    `extra_symbols` injects extra candidates (e.g. movers) into discovery; `book` = sizing account size
    (default 500); `account_id` selects the account for the exit scan and protection (default = your
    primary account). Draft-only — NEVER places; the owner places entries/exits (trade-placer /
    exit-placer with the codeword, or by hand in the Webull app); protective stops rest via the armed
    autopilot's PROTECT stage or are placed by hand (stop-market, never a stop-limit). Intended for an
    unattended Desktop scheduled task. Returns {entries, exits, protection, message}."""
    return morning_routine_run(book, account_id, symbols, extra_symbols)


# ── autopilot_run: the ONE unattended AUTO-PLACE surface (gate + caps + kill-switch) ──

def autopilot_run_tool(book: float = 400.0, account_id: str = "") -> str:
    """Run one autonomous cycle: reconcile -> protect -> exits -> entries, PLACING each order only
    when the autopilot gate allows (enable flag + kill-switch + per-order cap + max-positions +
    max-orders/day + daily-loss halt + SPY-regime; risk-reducing SELLs bypass the caps). OFF unless
    WEBULL_AUTOPILOT_ENABLED is set; every decision is audit-logged. This is the ONLY unattended
    real-order surface — distinct from the codeword place_order and the draft channel."""
    return _safe(lambda: autopilot_run_impl.run(book=book, account_id=account_id or None))


autopilot_run = mcp.tool()(autopilot_run_tool)
