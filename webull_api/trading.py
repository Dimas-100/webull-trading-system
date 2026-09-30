"""The only module that previews/places/cancels/modifies real orders.

Every order is validated by `safety` first; `place` always previews (non-executing)
and submits ONLY when `safety.should_submit(env, confirm)` is True.
"""
from __future__ import annotations

import time

from . import safety


def _safe_json(res):
    try:
        return res.json()
    except Exception:
        return {"status_code": getattr(res, "status_code", None), "text": getattr(res, "text", "")}


def _resolve(client, env):
    if client is None:
        from .client import trade_client
        client = trade_client()
    if env is None:
        from .client import get_settings
        env = get_settings().env
    return client, env


def _run_preview(client, account_id: str, order: dict):
    """Call Webull's non-executing server preview, capturing errors instead of raising.

    The SDK raises a ServerException on any non-200 (e.g. insufficient buying power),
    so a dry-run must catch it and surface the rejection rather than crash.
    """
    try:
        res = client.order_v2.preview_order(account_id, [order])
        return getattr(res, "status_code", "?"), _safe_json(res)
    except Exception as e:
        return "error", {"error": type(e).__name__, "message": str(e)[:300]}


def _print_preview(order: dict, env: str, status, body) -> None:
    qty, price = order.get("quantity"), order.get("limit_price", "MKT")
    max_cost = "n/a"
    try:
        if order.get("limit_price"):
            max_cost = f"{float(order['quantity']) * float(order['limit_price']):.2f}"
    except (TypeError, ValueError):
        pass
    print("------ ORDER PREVIEW ------")
    print(f"  ENV:        {env.upper()}")
    print(f"  {order.get('side')} {qty} {order.get('symbol')} @ {price} "
          f"{order.get('order_type')} {order.get('time_in_force')}")
    print(f"  est. max cost: {max_cost}")
    print(f"  server preview [{status}]: {body}")
    print("---------------------------")


def preview(account_id: str, order: dict, *, client=None, last_price=None):
    """Validate + run Webull's non-executing server-side preview. Never submits."""
    safety.validate_order(order, last_price=last_price)
    client, _ = _resolve(client, "test")
    _status, body = _run_preview(client, account_id, order)
    return body


def place(account_id: str, order: dict, *, confirm: bool = False, client=None,
          env=None, last_price=None) -> dict:
    """Dry-run by default. Submits only when confirm=True."""
    safety.validate_order(order, last_price=last_price)
    client, env = _resolve(client, env)
    status, body = _run_preview(client, account_id, order)
    _print_preview(order, env, status, body)
    if not safety.should_submit(env, confirm):
        print("DRY-RUN: confirm=False - NOT submitting.")
        return {"submitted": False, "preview": body}
    if env == "prod":
        print("\n*** SUBMITTING A LIVE REAL-MONEY ORDER TO PRODUCTION ***\n")
    res = client.order_v2.place_order(account_id, [order])
    try:    # observability only — a ledger failure must never affect the order result
        from . import exec_ledger
        exec_ledger.record_submit(account_id, order, env=env)
    except Exception:
        pass
    return {"submitted": True, "result": _safe_json(res)}


# ── Options (Phase 2) ─────────────────────────────────────────────────────────
# Parallel to preview()/place() above. REUSES safety.should_submit — the submit gate is
# unchanged; an option order submits only when confirm=True (prod needs env=prod + confirm).

def _run_option_preview(client, account_id: str, combo: dict):
    try:
        res = client.order_v2.preview_option(account_id, [combo], combo.get("client_order_id"))
        return getattr(res, "status_code", "?"), _safe_json(res)
    except Exception as e:
        return "error", {"error": type(e).__name__, "message": str(e)[:300]}


def preview_option(account_id: str, combo: dict, *, client=None):
    """Validate + Webull's non-executing preview for an option combo. Never submits."""
    safety.validate_option_combo(combo)
    client, _ = _resolve(client, "test")
    _status, body = _run_option_preview(client, account_id, combo)
    return body


def place_option(account_id: str, combo: dict, *, confirm: bool = False, client=None, env=None) -> dict:
    """Dry-run by default. Submits only when confirm=True (same gate as equities)."""
    safety.validate_option_combo(combo)
    client, env = _resolve(client, env)
    _status, body = _run_option_preview(client, account_id, combo)
    if not safety.should_submit(env, confirm):
        return {"submitted": False, "preview": body}
    if env == "prod":
        print("\n*** SUBMITTING A LIVE REAL-MONEY OPTION ORDER TO PRODUCTION ***\n")
    res = client.order_v2.place_option(account_id, [combo], combo.get("client_order_id"))
    return {"submitted": True, "result": _safe_json(res)}


def cancel(account_id: str, client_order_id: str, *, client=None):
    client, _ = _resolve(client, "test")
    return _safe_json(client.order_v2.cancel_order(account_id, client_order_id))


def modify(account_id: str, modify_order: dict, *, client=None):
    """modify_order = {'client_order_id': ..., 'quantity': ..., 'limit_price': ...}."""
    client, _ = _resolve(client, "test")
    return _safe_json(client.order_v2.replace_order(account_id, [modify_order]))


def get_open_orders(account_id: str, *, client=None):
    """Read-only: today's open orders for the account."""
    client, _ = _resolve(client, "test")
    return _safe_json(client.order_v2.get_order_open(account_id))


# Order history is PAGED (2026-09-18). The SDK's get_order_history returns 10 rows (its
# page_size default) over the last 7 days, and every consumer here — journal sync, the flows
# runner, the web app, the copilot tool — read that one page as the whole window. A five-lot
# week silently pushed fills out of the page: the 09-16 LRCX entry vanished from the flows
# runner's traded notional and was booked as a phantom owner withdrawal.
HISTORY_PAGE_SIZE = 100          # the broker's maximum (live 2026-09-18: 417 outside 10..100)
HISTORY_PAGE_MIN = 10
HISTORY_MAX_PAGES = 20
HISTORY_PAGE_GAP_S = 1.2         # order queries allow ~2 reads per 2-3 s; back-to-back pages 429
HISTORY_RETRY_TRIES = 3          # bounded, TOO_MANY_REQUESTS only (same shape as autopilot/run.py)
HISTORY_RETRY_WAIT_S = 4.0


def _history_call(ops, account_id: str, kwargs: dict, sleep_fn):
    """One page, retried on the broker's throttle only; any other error propagates at once."""
    for attempt in range(1, HISTORY_RETRY_TRIES + 1):
        try:
            return _safe_json(ops.get_order_history(account_id, **kwargs))
        except Exception as e:
            if attempt == HISTORY_RETRY_TRIES or "TOO_MANY_REQUESTS" not in str(e):
                raise
            sleep_fn(HISTORY_RETRY_WAIT_S)


def _envelope_ids(envelope) -> tuple[str | None, str | None]:
    """(client_order_id, order_id) of one history envelope: its own ids, else its last leg's
    (combo envelopes are {orders: [leg...]}). (None, None) when it carries neither."""
    if not isinstance(envelope, dict):
        return None, None
    legs = envelope.get("orders")
    leg = legs[-1] if isinstance(legs, list) and legs and isinstance(legs[-1], dict) else {}
    cid = envelope.get("client_order_id") or leg.get("client_order_id")
    oid = envelope.get("order_id") or leg.get("order_id")
    return (str(cid) if cid else None), (str(oid) if oid else None)


def _history_page(raw) -> list:
    """The envelopes of one page. Anything but a list or {data: [...]} (e.g. _safe_json's
    fallback for a non-JSON 200) RAISES: read as 'no orders' it would end the walk silently and
    the flows runner would book an owner flow from a window it never saw."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and "data" in raw and (raw["data"] is None or isinstance(raw["data"], list)):
        return raw["data"] or []
    raise RuntimeError(f"order history: unrecognised page body ({type(raw).__name__}) — "
                       "refusing to read it as an empty page")


def get_order_history(account_id: str, *, client=None, page_size: int | None = None,
                      max_pages: int | None = None, sleep_fn=None) -> list:
    """Read-only: the account's order history, paged to completion.

    Each page resumes from the previous page's last (client_order_id, order_id) — the SDK's
    cursor. A page shorter than the requested size is the last one (the broker caps page_size
    at 100 and the read never asks for more); an exactly-full page is followed by one more
    read, which ends on an empty page or one that repeats what was already returned (a broker
    that ignores the cursor). Pages are spaced and a throttled page is retried a bounded number
    of times. Hitting `max_pages` raises: every caller treats the result as the whole window,
    and a silently short list is the defect this fixes (2026-09-18).
    """
    client, _ = _resolve(client, "test")
    sleep = sleep_fn or time.sleep
    size = max(HISTORY_PAGE_MIN, min(HISTORY_PAGE_SIZE, int(page_size or HISTORY_PAGE_SIZE)))
    limit = int(max_pages or HISTORY_MAX_PAGES)
    out: list = []
    seen: set = set()
    cursor: tuple = (None, None)
    for _page in range(limit):
        kwargs: dict = {"page_size": size}
        if cursor != (None, None):
            kwargs["last_client_order_id"] = cursor[0]
            kwargs["last_order_id"] = cursor[1]
            sleep(HISTORY_PAGE_GAP_S)
        page = _history_page(_history_call(client.order_v2, account_id, kwargs, sleep))
        if not page:
            return out
        fresh = []
        for env in page:
            key = _envelope_ids(env)
            if key != (None, None):
                if key in seen:
                    continue
                seen.add(key)
            fresh.append(env)
        if not fresh:
            return out                        # the cursor was ignored: same page again
        out.extend(fresh)
        if len(page) < size:
            # A short page is the last page. Residual risk noted: the SDK says a page of GROUP
            # orders "may exceed the page_size"; if the broker ever counted legs and returned
            # fewer envelopes with more remaining, this would stop early. The live 10-row and
            # 100-row walks agreeing (13 envelopes) is the evidence for envelope counting.
            return out
        last = _envelope_ids(page[-1])
        if last == (None, None):
            raise RuntimeError(f"order history for {account_id}: a full page ended in an "
                               "envelope without ids — cannot resume; refusing a truncated window")
        if last == cursor:
            return out                        # the broker re-served the cursor row: done
        cursor = last
    raise RuntimeError(f"order history for {account_id}: more than {limit} pages of {size} — "
                       "refusing to return a truncated window")
