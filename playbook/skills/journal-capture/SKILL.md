---
name: journal-capture
description: >-
  Use when the owner reports a fill or trade to log — "log: bought 4 SOFI @
  16.20," "journal this," "sold the SOFI at 17.10," "I got stopped out" — from
  a phone session or any session. Appends to playbook/journal.md and nothing
  else. NOT for deciding a trade (trade-planner), reviewing the book
  (trade-review), or placing/drafting anything (never from here).
---

# Journal Capture (log a fill in seconds)

## What this is

The fast path from "I just filled" to a template-true entry in
`playbook/journal.md`. The whole job is: parse the owner's message → append (or
complete) ONE entry → echo it back. **Target: one read + one edit.** No broker
calls, no market data, no cross-checks — capture trusts the owner's numbers;
reconciliation against broker fills is a later desktop step (the journal
header's contract), not this moment's.

**The journal is the honesty engine: never refuse to log a real fill.** Even a
trade the screen gated out gets logged — discipline is scored at `trade-review`,
not litigated at capture. Log first, flag quietly, never lecture.

## Entry lifecycle

An entry is logged **open** at the fill and **completed in place** at the close.
Newest first: insert directly under the file's header block.

**Open (a BUY / new position):** exact template fields — no additions, no
omissions. `date:` = the **fill date (ET)**, not the logging date. Objective
fields the owner didn't state → `null` (never estimate; they are reconciled
later). `risk_planned` = shares × (entry − stop) when a stop is stated.

````markdown
## 2026-07-17 · SOFI · long · OPEN · pullback-in-trend

```yaml
date: 2026-07-17
ticker: SOFI
side: long
entry: 16.20
exit: null
shares: 4
pnl: null
pnl_pct: null
r_multiple: null
risk_planned: 5.20
setup: pullback-in-trend
plan_followed: null
result: open
hold: open
```

**Why I took it:** Owner: "plan was the swing setup from Tuesday's screen"
(playbook/screens/2026-07-14 — not on disk; flag at review). Stop 14.90, per plan.
**Did well / poorly:** —
**Lesson:** —
````

**Close (a SELL / "stopped out"):** find the newest open entry for that ticker
and complete it in place — fill `exit`, `pnl` ((exit − entry) × shares, sign
flipped for short), `pnl_pct`, `r_multiple` (pnl ÷ risk_planned, else `null`),
`result` (win/loss/scratch), `hold` (intraday/swing from the dates); retitle the
heading `· +$3.60 (+5.6%) · <setup>`; keep the entry-date in the heading. Set
`plan_followed` only if the owner says (or their message makes it plain — "sold
early," "moved my stop" → `false`); else leave `null` for review. Invite one
line for **Did well / Lesson**; if none comes, leave `—`.

No matching open entry? Append a fresh entry with both sides filled from what
the owner gave, `null` for the rest — don't interrogate.

## Refuse from capture — always

- **Placing or drafting ANY order.** "Put the stop in at 14.90 too" is either a
  log of what the owner already did (record it in the narrative) or a request —
  and the answer to the request is: *logged; I can't place from here — put the
  stop in the Webull app.* Never route to `trade-placer`/`exit-placer`, never
  draft "so it's ready."
- **Verifying against the broker.** No broker/API calls at capture, even
  "just to check the stop is resting." Say the numbers are owner-reported.
- **Writing anywhere but `playbook/journal.md`.** Not `data/plans/`, not
  `data/intents/`, not `data/journal/` — those are the systems' stores.
- **Editing the plans, charter, skills, or old entries** (corrections happen
  only when the owner explicitly asks to fix a stated entry).
- **Schema drift.** No new YAML fields, ever — extra context (stop, target,
  venue, doubts) goes in the narrative lines.

## Red flags — you're about to break capture

- "Let me just verify the fill via the API first" → capture trusts, desktop reconciles.
- "I'll draft the stop so it's waiting for them" → no drafts from capture.
- "The screen gated this out — should I push back?" → log it; one neutral flag
  in **Why I took it**; review judges.
- "I'll add a `stop:` field" → narrative, not schema.
- More than ~2 tool operations before the entry exists → you're investigating,
  not capturing.

## After the append

Echo the entry (heading + YAML) so the owner can eyeball it from the phone. If
their message mentioned a protective stop, close with one line: the stop is
recorded here, **not resting anywhere by this session** — place it in the app
or at the desktop if it isn't already.
