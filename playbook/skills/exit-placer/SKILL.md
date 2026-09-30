---
name: exit-placer
description: >-
  Use when closing or exiting a Webull swing position — "close my PLUG now,"
  "sell my X," "anything I need to close," "I got stopped out," or when a
  position has hit its stop, target, or trend break. Places (or drafts) a
  full-close SELL through the codeword-gated place_order, and holds the line on
  exit discipline — a tripped stop gets placed, never lowered or averaged down.
  Stocks only.
---

# Exit Placer (Webull — the close-the-position step)

## What this is

The mirror of `trade-placer`: that one **opens** a position, this one **closes**
one. It places a full-close SELL — for a position you name, or one the exit scan
flags — through the same codeword gate, with pre-flight checks and a draft
fallback (no automated confirmation path — the owner places a draft by hand).

Its real job is **exit discipline.** Exits are where the plan gets broken: the
stop is non-negotiable (`swing-trading-plan.md` §5), and the cardinal sin (§10)
is moving it down or averaging into a loser. This skill holds that line.

**This places a LIVE order with real money.** Treat every run as irreversible.

## When to use

- "Close my X / sell my X / get me out of X now" — exit a specific position.
- "Anything I need to close? / check my exits" — scan open positions for tripped
  stop / target / trend-break signals and act on them.
- "I'm down past my stop / I got stopped out" — place the exit.
- **Not** for: deciding strategy or setting the original stop (`trade-planner` /
  the plan), placing **entries** (`trade-placer`), **options** (the owner places
  those by hand in the app), or intraday management (this is EOD).

## Step 1 — what are we exiting?

- **Named position** ("close PLUG"): take that position, full close → Step 2.
- **Rule scan** ("anything to close?"): run the exit scan (the `manage_exits`
  rule set / `exits.py`) and surface each tripped position with its **reason**
  (`stop` ≤ −8% · `target` ≥ +20% · `break` freshly CROSSED below the 20-day SMA
  — latest close below it AND the prior close at/above it; a position that broke
  days ago and still sits under its SMA does NOT re-trip the scan, so "no
  signal" ≠ "above its 20-day") and unrealized %. The user picks which to act on.

The −8% / +20% / break thresholds are the **automated backstop.** The trader's
own **structural stop from the plan (swing-low − 0.25×ATR), set at entry, is the
primary rule** — if they say they're at their stop, that's the trigger, scan or
no scan.

## Step 2 — build and SHOW the close, get an explicit go

- Full-close SELL · `quantity` = the whole position · `order_type` **LIMIT**
  (price it to **fill** — at or just below last, i.e. marketable — an exit you
  let rest above the market is an exit that doesn't happen) · `limit_price` ·
  `time_in_force` (DAY, or GTC after hours).
- Show it back in full with the **reason** (stop / target / break / time-stop /
  manual) and the unrealized %. Get an explicit **"yes, close it."**

## Step 3 — pre-flight sanity

- **Session vs TIF.** A DAY order outside 9:30–16:00 ET auto-rejects
  (`DAY_ORDER_NOT_ALLOWED_AFT_CORE_TIME_LIMIT`). After hours → GTC.
- **Limit vs last.** For a SELL, a limit **above** the market won't fill — an
  exit needs to fill, so price at/just-below last (or marketable). Flag a limit
  set so high it would just rest.
- (Funding isn't a constraint on a sell.)

## Step 4 — the codeword gate (do not skip, do not improvise)

Direct placement requires the user's secret codeword.

- **Ask the user to supply it.** You do **NOT** know it — **never guess, invent,
  assume, default, store, echo, or log it.** No codeword this turn → stop and ask.
- Pass it **verbatim** to `place_order` as the `codeword` argument (exactly what
  the user typed). Do not repeat it back.

## Step 5 — place or draft, and handle every outcome

Place direct with `place_order(side="SELL", …, codeword)`, or draft instead
(`manage_exits` for the rule-based bulk close, or `draft_order` for one) — the
draft lands in `data/intents/` (JSON, no display surface) and the owner places
it by hand in the app. React to what returns:

| Result | Means | Do |
|---|---|---|
| `placed: True` | LIVE close submitted | Report it plainly (symbol/qty/price + `notional`); note it **auto-journaled** (`journaled`). |
| `BadCodeword` | codeword didn't match | Say so; offer **one** retry. Reveal nothing about the secret. |
| `OverCap` | the close notional exceeds the per-order cap | **Don't force it.** Draft instead (`manage_exits` / `draft_order`) — the owner places it by hand. |
| `DirectTradingDisabled` | `WEBULL_TRADE_CODEWORD` not set in `.env` | Direct placing is off — draft instead (same hand-off). |
| `CapUnverifiable` | no usable price for the cap check — a MARKET order whose snapshot lookup failed, or any plain STOP_LOSS (stop-market, no limit) | Use a marketable LIMIT (or STOP_LOSS_LIMIT), or draft — **for a full-close EXIT only, never for a protective stop.** A protective stop stays a stop-market: the armed autopilot's PROTECT stage rests it, or the owner places it by hand in the Webull app (a stop-limit can fail to fill on a gap-down; a marketable LIMIT SELL closes the position instead of protecting it). |

Never retry automatically, never work around the cap, never substitute a codeword.

## Step 6 — report

State the outcome flatly: a LIVE close was **placed** (result + notional +
journaled) or **drafted** for the owner to place by hand, with the reason. If a
gate stopped it, name the gate and the safe next step.

**Journal it honestly.** The fill auto-journals, but the *grade* is yours: if the
position was held **past** the stop (e.g. stopped at −8% but exited at −12%), or
the name never qualified (below the `$10` price floor, ATR too high — §3), that's
`plan_followed: false`, not a clean loss. Name the one upstream fix (set the stop
as a resting order at entry; enforce the screen) — that's the part that compounds.
The full post-mortem is `trade-review`, not this skill; just don't let a
plan-break get logged as a plan-followed loss.

## The discipline spine — hold the line

This is the part a generic agent gets wrong:

- **A tripped stop gets placed, not debated.** "It'll bounce," "the chart feels
  like it's turning," "just a little more room" — that's noise, not new
  information (the plan: drawdown is not new information). If the stop is hit,
  drive to the exit.
- **Refuse the cardinal sins, by name.** Do **not** help move a stop **down** /
  "give it room," and do **not** help **average down** a loser — both are
  forbidden by `swing-trading-plan.md` §5/§10. Name the rule and stop. If the
  user insists, that itch is the rule doing its job — say so; don't tee up the
  order.
- The stop only ever moves **UP** (to lock gains), never down.

## What this does not do

- Decide strategy or set the original stop (`trade-planner` / the plan).
- Place **entries** (`trade-placer`).
- Move a stop **down** or **average down** (forbidden — it refuses).
- Manage intraday (EOD discipline).
- Place **options** exits (the owner places those by hand in the app).
- Modify or cancel resting orders — closing positions only.
