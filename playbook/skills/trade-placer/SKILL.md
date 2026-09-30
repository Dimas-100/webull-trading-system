---
name: trade-placer
description: >-
  Use when placing a small, already-decided US equity/ETF trade in Webull right
  now — "place this," "buy 1 share of AAPL now," "submit that order." Places a
  LIVE order through the codeword-gated place_order tool (you supply the secret
  codeword; it is never guessed), within the per-order cap; over the cap or
  unconfigured, it drafts to data/intents/ (JSON) for the owner to place by hand.
  Stocks only — options are not supported yet. For closing/exiting an open
  position ("sell my X"), use exit-placer, which adds the exit-discipline rules.
---

# Trade Placer (Webull — the live-order step)

## What this is

The one skill that actually **places** a trade. Every other Webull skill stops at
"you place it in Webull"; this is that step, for a **small, already-decided US
equity/ETF order**. It drives `place_order` (the project's single quarantined
direct real-order surface): it shows you the exact order, makes **you** supply the
secret codeword, respects the per-order cap, and drafts anything it can't place
directly to `data/intents/` (JSON) — there is no automated confirmation path for
that draft; the owner places it by hand in the app.

It executes a decision — it does **not** make one. If the trade isn't decided yet,
that's `trade-planner`, not this.

**This places a LIVE order with real money.** Treat every run as irreversible.

## When to use

- "Place this / submit that / buy 1 share of X now" — a specific,
  already-decided equity trade you want to execute.
- After `trade-planner` returns a plan and you want to put the small entry on.
- **Not** for: deciding whether to trade (`trade-planner`), **options** (deferred
  — there is no codeword-gated option surface yet; the owner places it by hand in
  the app), closing or managing open positions ("sell my X" → **`exit-placer`**, which adds
  the exit-discipline rules; scans → `manage_exits` / `trade-review`), or anything
  over the per-order cap (it drafts instead — the owner places it by hand).

## Step 1 — preconditions

- **Decided already?** This skill assumes the trade is decided. If the user is
  still asking "should I" / "how many shares" / "where's my stop," **stop and run
  `trade-planner`** — don't place an unplanned trade.
- **Equity/ETF only.** If the symbol is an option contract, **stop**: options
  aren't supported here yet; the owner places it by hand in the app.

## Step 2 — build and SHOW the order, get an explicit go

Assemble the order and show it back in full before anything else:

- `symbol` · `side` (BUY/SELL) · `quantity` (whole shares) · `order_type`
  (**default LIMIT**) · `limit_price` / `stop_price` · `time_in_force` (DAY, or
  **GTC** after hours) · estimated **notional** (`quantity × limit_price`).
- **Prefer LIMIT.** A MARKET order is cap-checked against the live snapshot last
  price — that normally works, but if the snapshot lookup fails the order is
  refused (`CapUnverifiable`), and the fill price itself is unbounded. Only use
  MARKET if the user insists, and warn them. A plain **STOP_LOSS** (stop-market)
  order carries no price at all and is **always** refused `CapUnverifiable` —
  use **STOP_LOSS_LIMIT** (add a limit) instead.
- Get an explicit **"yes, place this"** on the exact order. No confirmation → stop.

## Step 3 — pre-flight sanity (will this order actually work?)

Quick read-only checks before the gate — each catches a failure that wastes a
real order (all three learned on live orders):

- **Session vs TIF.** A **DAY** order outside regular hours (9:30–16:00 ET) is
  auto-rejected by Webull (`DAY_ORDER_NOT_ALLOWED_AFT_CORE_TIME_LIMIT`). After
  hours → use **GTC**.
- **Funding (BUYs).** Check the account's buying power (account/portfolio tools)
  covers the notional. If it doesn't, Webull rejects on submit — flag it now, not
  at the gate.
- **Limit vs last.** Pull the last price (market-data tools) and compare. A BUY
  limit far **below** market (or a SELL far **above**) won't fill, and a limit
  wildly off market is a likely fat-finger. Flag it and confirm the price is
  intentional.

Raise any flag and get the user's go before proceeding. None of these
re-litigate the decision — they're about whether the order does what's intended.

## Step 4 — the codeword gate (do not skip, do not improvise)

The live placement requires the user's secret codeword.

- **Ask the user to supply it.** You do **NOT** know it — **never guess, invent,
  assume, default, store, echo, or log it.** If the user hasn't given it this
  turn, stop and ask for it; do not proceed without it.
- Pass it **verbatim** to `place_order` as the `codeword` argument (exactly what
  the user typed). Do not repeat it back in your reply.

This mirrors the tool's own contract: it is the human-in-the-loop authorization,
and it is the user's alone.

## Step 5 — place via `place_order`, and handle every outcome

Call `place_order` with the order fields + the codeword. React to what returns:

| Result | What it means | Do |
|---|---|---|
| `placed: True` | LIVE order submitted | Report it plainly — symbol/side/qty/price + `notional`; note it **auto-journaled** (`journaled` count). |
| `BadCodeword` | codeword didn't match | Say it didn't match; offer **one** retry (user re-supplies). Reveal nothing about the secret. |
| `OverCap` | notional exceeds the per-order cap (`WEBULL_TRADE_MAX_NOTIONAL`) | **Don't force it.** Offer to **`draft_order`** instead → it lands in `data/intents/` (JSON, no display surface); the owner places it by hand in the app. |
| `DirectTradingDisabled` | `WEBULL_TRADE_CODEWORD` isn't set in `.env` | Explain direct placing isn't enabled yet — they set `WEBULL_TRADE_CODEWORD` in the Webull `.env` to turn it on. Offer to **`draft_order`** meanwhile (same hand-off). |
| `CapUnverifiable` | the order carries no usable price for the cap check — a MARKET order whose snapshot lookup failed, or any plain STOP_LOSS (stop-market, no limit) | Switch to a **LIMIT** (or **STOP_LOSS_LIMIT**) order, or **`draft_order`**. |

Never retry a placement automatically, never escalate around the cap, never
substitute your own codeword.

## Step 6 — report

State the outcome flatly: a LIVE order was **placed** (with the fill/result and
notional) or **drafted** to `data/intents/` for the owner to place by hand. If it
errored, say which gate stopped it and the safe next step. The fill auto-journals
on a successful place; surface the journaled count.

## What this does not do

- **Decide** the trade — that's `trade-planner` (gates, sizing, R:R). This only
  executes an already-made decision.
- Place **options** — deferred; the owner places option orders by hand in the app.
- **Bypass** the codeword or the per-order cap — over-cap / unpriceable /
  not-configured always falls back to a hand-placed order (draft it for the record).
- Manage open positions or exits (`manage_exits` / `trade-review`).
- Modify or cancel resting orders — placement only.
