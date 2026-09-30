---
name: trade-review
description: >-
  Use when reviewing Webull swing trades — "how am I doing," "review my trades,"
  "what's my win rate / expectancy," "weekly review," "system review," "am I
  following my plan," or when it's time for the weekly or every-20-trades review
  of the trading journal.
---

# Trade Review (Webull swing book)

## What this is

Reads `playbook/journal.md`, computes the swing plan's §9 metrics, and frames the
review the way the plan demands: **plan-followed % is the headline, P&L is judged
last.** It auto-detects whether a full system review is due, surfaces the biggest
rule-break, and logs each review to `playbook/reviews.md`.

It reviews — it doesn't trade. And it will **not** touch the rulebook between
scheduled reviews (the plan is frozen; that immutability is its spine).

## When to use

- Weekly tally, or the every-20-trades / monthly system review.
- "How am I doing / what's my expectancy / am I following the plan."
- **Not** for planning a new trade (`trade-planner`) or a single-name thesis
  (`investment-research`).

## Inputs / Reads

- `playbook/journal.md` — the swing trading journal (closed round-trips with fenced-yaml fields).
- `data/activity/scorecard_history.jsonl` — the weekly per-strategy scorecard (buckets,
  expression A/B, rule flags). Start the review from its latest row's flags.
- `playbook/reviews.md` — the historical review log (for cadence tracking).

## Step 1 — read the journal

Parse the closed round-trips in `playbook/journal.md` (the fenced-yaml fields:
`r_multiple`, `risk_planned`, `setup`, `plan_followed`, `result`, `pnl`, `hold`).
**Skip the `EXAMPLE` / template entry.** If there are no real trades yet, say so
and stop — there's nothing to review.

## Step 2 — compute the §9 metrics

| Metric | How |
|--------|-----|
| Closed trades | count |
| Win rate % | wins ÷ trades (R > 0 = win, R < 0 = loss, R = 0 = scratch) |
| Avg win R / Avg loss R | mean R of wins; mean \|R\| of losses |
| **Expectancy (R/trade)** | `(Win% × AvgWinR) − (Loss% × AvgLossR)` — equals total R ÷ trades |
| Profit factor | gross win $ ÷ gross loss $ |
| Max drawdown % | peak-to-trough of the running book |
| **Plan-followed %** | `plan_followed: true` ÷ trades (target **≥ 90%**) |
| % valid setups | Gate-1–5 setups ÷ trades (vs forced / drifted) |

## Step 3 — frame it the plan's way (don't bury the lede)

- **Lead with plan-followed %, not P&L.** In the first ~50 trades this is *the*
  metric. A profitable stretch with sub-90% adherence is a **warning sign, not a
  win** — it means you're getting paid for undisciplined behavior that reverses.
- **A rule-breaking winner is a bad trade** (`plan_followed: false` with R > 0).
  Call it out by name — it's the most dangerous entry because it teaches the
  wrong lesson. Don't let it inflate the "win" story.
- **Sample-size discipline:** give **no system verdict under ~30 closed trades.**
  Compute the metrics, but a 5-trade streak (either way) means nothing — say so
  loudly. The journal exists to answer "do I have an edge" over 30–50+ trades,
  not this week.

## Step 4 — the biggest rule-break + patterns

Name the **single biggest rule-break** this period, and any recurring pattern,
tied to the §10 traps: stop moved *down* (the cardinal sin), setup drift, holding
through a time-stop or earnings, oversizing past 2%, averaging down. One clear
call-out beats a list.

## Step 5 — cadence: is a system review due?

A full **system review** is due at **≥ 20 closed trades since the last one, OR
~1 month (≈28–31 days) elapsed since the last** — whichever comes first (the
plan's "next review" line). Find the date + trade-count of the last review in
`playbook/reviews.md`; if there's none yet, use the plan's effective date.

**Compute and STATE both numbers — do not eyeball "about a month." Subtract the
actual dates:**

- trades since last review = **N** (due if `N ≥ 20`)
- days since last review = **D** (due if `D ≥ ~28`)

Due only if **`N ≥ 20` OR `D ≥ ~28`.** If *both* are below the line → **NOT due.**

- **Not due** → this is a weekly tally. **Rules are FROZEN** — do not propose or
  draft any rule change. State how far off the review is (e.g. "12 more trades,
  or ~17 more days").
- **Due** → Step 6 unlocks.

## Step 6 — rule changes (ONLY when a system review is due)

If the data flags a candidate rule issue (e.g. stops too tight → wicked out
repeatedly; a gate that never fires), surface it, and **offer to draft the change
into `playbook/swing-trading-plan.md`** with a **reason + effective date** (the
plan requires every change be written there with both). Never mid-stream, never
curve-fit to the last loss — that's the failure mode the immutability rule exists
to stop.

## Step 7 — log the review

Append to `playbook/reviews.md` (create it if missing) so adherence and expectancy
are trackable over time — offer it, write on a yes:

- **Weekly tally** → a terse one-liner: date · `weekly` · # trades · plan-followed % · biggest rule-break.
- **System review** → the full entry: date · `system` · # trades · the metric set · plan-followed % · biggest rule-break · any rule change made.

Keeping weekly entries to one line stops the log from bloating into low-signal
noise (the same reason the research folder doesn't save "nothing changed" briefs).

## What this does not do

It doesn't plan trades (`trade-planner`), doesn't change rules outside a scheduled
review, and doesn't judge the system on a small sample — it reports the metrics,
leads with adherence, and keeps the rulebook frozen between reviews.
