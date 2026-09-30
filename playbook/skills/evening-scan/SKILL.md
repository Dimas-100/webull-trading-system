---
name: evening-scan
description: >-
  Use when running the evening trading routine for the Claude-managed Webull
  account — "run the evening scan," "find me candidates for tomorrow," "what
  should I trade," "do the nightly scan," "scan the market." Sources candidates
  itself (this account is Claude-managed — NOT the user's watchlist): pulls the
  market-wide movers feed, hard-filters it, injects it into discover_and_draft
  alongside the curated universe, drafts any swing PASSES, checks open positions
  for exits, and presents everything for the user's review + codeword. Draft-only
  — it never places; entries flow to trade-placer, exits to exit-placer (both
  through the codeword). Governed by docs/proof-phase-charter.md.
---

# Evening Scan (the nightly candidate routine)

## What this is

The once-a-day routine for the **Claude-managed** proof-phase account. Because the
account is Claude-managed, *finding what to trade is the system's job* — this skill
sources candidates itself instead of reading a watchlist. It runs after market close
(the user's PC is off during the day; swing decisions are end-of-day anyway), drafts
resting-order candidates for the next session, and hands them to the user to review
and authorize.

It is **draft-only**. It never places an order. Entries land as a pending JSON draft
in `data/intents/` (no display surface); place them via `trade-placer` with the codeword.
Exits go through `exit-placer`, also codeword-gated. The submit gate is untouched.

Read `docs/proof-phase-charter.md` first — it sets the book size, per-order cap, max
positions, and the scale-up bar this routine operates under.

## When to use

- The user asks to run the evening/nightly scan, find candidates, or prep tomorrow.
- Any time you're sourcing swing entries for the managed account.

Do NOT use for options (equities only here) or for placing a decided trade (that's
`trade-placer` / `exit-placer`).

## The routine

Use the **real book size from the charter** (proof phase = **$400**) for `book`.

### 1. Pull the market-wide movers feed (agent-layer bridge)

The toolkit's SDK has no movers endpoint, so movers come from the **official Webull
connector** (only the agent can bridge the two). Call:

- `get_most_active` (category US_STOCK)
- `get_gainers_losers` (direction=DESC for gainers; optionally ASC for losers)

### 2. Hard-filter the movers (they're noisy for a pullback strategy)

Raw movers are dominated by sub-$1 pump stocks and momentum gaps — the *opposite* of
a pullback-in-uptrend setup. Keep only names that are plausibly tradable, at the agent
layer, before injecting:

- **Price in band:** `$10 <= price <= 0.20 * book` (the planner's ceiling is 20% of
  book — $80 at a $400 book). Drop anything outside.
- **Drop junk:** sub-$1 names, obvious leveraged/inverse ETFs, tickers with `.`/warrants.
- Keep the **~10-15** most relevant survivors. Their only job is to surface fresh liquid
  names the static core doesn't have — the swing planner still judges every one.

### 3. Discover + draft entries

Pass the filtered movers as `extra_symbols` (comma-separated) to the toolkit tool:

- `discover_and_draft(book=<charter book>, extra_symbols="<CSV of filtered movers>")`

It scans the curated universe + your injected movers, filters to band (dedup + the
20%-of-book ceiling are automatic), runs the swing screen, and drafts every PASS as a
GTC buy-stop-limit into `data/intents/` (JSON; nothing renders it). Returns `{discovered,
scanned, in_band, drafted, skipped, errors}`.

### 4. Check open positions for exits

- `manage_exits(account_id="", stop_loss_pct=8.0, take_profit_pct=20.0)`

Drafts a full-close SELL for any position tripping stop / target / trend-break. Held
positions are reported, not drafted.

### 4.5 Protect open positions (resting stops)

- `protect_positions(account_id="", stop_loss_pct=8.0)`

Drafts a resting **GTC** protective stop (SELL STOP_LOSS, stop-market) for any open position that has **no
resting stop at the broker** — so downside stays covered while the PC is off (charter §5). Uses each
position's plan-of-record structural stop when known, else the -8% backstop. `needs_manual` names any
position that couldn't be priced (no cost basis / no plan) — surface those for a hand-set stop. This is
protection, not an exit signal: a protective stop is **placed, never lowered**. It is NOT placed through
`exit-placer` or the codeword `place_order` (that path refuses an unpriced STOP_LOSS with
`CapUnverifiable`): on system lots the armed autopilot's PROTECT stage rests it; otherwise the owner
places the stop-market by hand in the Webull app. **Never convert a protective stop to a stop-limit (or a
marketable LIMIT) to get past the cap check** — a stop-limit can fail to fill on a gap-down; a marketable
LIMIT SELL closes the position instead of protecting it.

### 4.75 Red-team each drafted entry (fight the trade)

For every entry the swing screen drafted (the `drafted[].symbol` from `discover_and_draft` / `screen_and_draft`),
run the **`red-team`** skill: it builds an independent bear case (failure modes, invalidation
triggers, journal history, event risk) + a `kill`/`caution`/`proceed` verdict and writes it
onto the draft via `annotate_intent` (JSON; no cockpit shows it).
This is the "fight the trade" step — an independent check before you authorize. It's advisory:
a `kill` is a strong flag, not an action; you still decide.

### 5. Present for review (don't place)

Summarize for the user:

- **Entries drafted:** symbol, entry (buy-stop), stop, target, shares, R:R, risk%.
- **Bear case per entry:** the red-team verdict (kill/caution/proceed) + its top objection.
  A `kill` deserves a hard second look before you hand over the codeword to place it.
- **Exits drafted:** symbol, reason (stop/target/break), qty.
- **Protective stops drafted:** symbol, stop price, source (structural / -8% backstop); plus any
  `needs_manual` positions that still need a hand-set stop.
- **Notable skips:** a few, with the gate reason — this shows the discipline is working.
- **Counts:** `scanned / in_band / drafted`.

**"0 drafted" is a normal, healthy result** — the swing plan is strict and most days
pass nothing. Never force a trade to have something to show.

### 5.5 Save the writeup (REQUIRED — the durable record)

Write the evening's summary to **`playbook/screens/YYYY-MM-DD-eod-screen.md`** (ET date),
matching the existing files' shape: header (book · strategy · band · draft-only), **Outcome**,
**Counts** (movers pulled / in-band injected / drafted / exit drafts / stop drafts), injected
movers, entry skips with gate reasons, positions, **Action for Dimas**. A run that isn't saved
is invisible afterward — the phone `status-checkin` treats the newest file here as the newest
scan, and `trade-review` cross-checks entries against these screens. Presenting without saving
is an incomplete run.

### 6. Handoff

- Entries: the user reviews each draft (JSON) and, if they hand you the codeword,
  you place it via `trade-placer` (cap-respecting). Over the cap, or no codeword set:
  there is no automated fallback — the user places it by hand in the Webull app.
- Exits: route through `exit-placer` (a tripped stop is placed, never lowered, never
  averaged down).
- Protective stops: a resting GTC protective stop is **placed, never lowered** — the armed
  autopilot's PROTECT stage rests it on system lots; otherwise the user places the stop-market
  by hand in the Webull app. Not through `exit-placer` / the codeword (it refuses an unpriced
  STOP_LOSS), and never converted to a stop-limit or LIMIT to get past the cap check.

## Guardrails

- **Draft-only. Never place from this skill.** No `place_order` here — placement is a
  separate, explicit, user-authorized step.
- **The planner is the arbiter.** Don't second-guess a SKIP or hand-pick around the
  rules; if a name feels compelling but skipped, that's a `trade-planner` conversation.
- **Respect the charter:** book size, per-order cap, max 3 open positions, scale-up bar.
- **Movers are a secondary source.** If the official connector is unavailable, run
  `discover_and_draft` with no `extra_symbols` — the curated core still works.
