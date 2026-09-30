---
name: red-team
description: >-
  Use to argue AGAINST a candidate trade — "red-team this," "fight this trade,"
  "argue against AMD," "what's the bear case," "why would this lose". Builds an
  independent, skeptical bear case for a swing entry (pre-mortem, falsification,
  journal history, event risk) + a kill/caution/proceed verdict, and writes it
  onto the pending Desktop draft so it's there before you place. Read-only
  and ADVISORY — it never places, dismisses, or re-plans; you hold the veto.
  Invoked by evening-scan per drafted PASS, or on demand before any entry.
---

# Red-team (fight the trade)

## What this is

The devil's advocate. For a candidate swing **entry**, it does the opposite of the
screener: it tries to **refute** the trade. Its job is to counter confirmation bias
and the pipeline's permissive-on-missing-data tendency by putting a concrete bear
case in front of you *before* you authorize. It is **read-only and advisory** — it
argues; **you** decide (charter §3 veto). It does not re-plan (that's `trade-planner`)
or place anything (that's `trade-placer`).

## When to use

- `evening-scan` invokes it on each drafted PASS.
- On demand: "red-team X," "what's the bear case for this," before a manual entry.

Do NOT use it to argue against an **exit** — an exit is rule-based; you don't debate a
stop (that's `exit-placer`'s discipline). Entries only.

## Method — gather evidence, then refute

For the symbol, pull read-only evidence via the official **`webull`** connector: daily bars
(`get_stock_bars`) for trend, levels and relative strength vs SPY; `get_stock_snapshot`;
`get_stock_earnings_calendar`; `get_analyst_rating` / `get_analyst_target_price`. (The
`webull-analysis` connector was archived 2026-09-28 — docs/ARCHIVE.md.)
Then build the bear case — **default to skepticism; do not write a balanced note:**

1. **Top 2–3 failure modes** — concrete ways *this* trade loses (level break, RS
   rollover, sector weakness, thin liquidity, crowded/extended, gap risk).
2. **Thesis-invalidation triggers** — what would prove the setup wrong (a price level, a
   failed follow-through, an event).
3. **Journal check** — if you can read `playbook/journal.md`, look for past trades on this
   **symbol or setup** and their discipline flags; otherwise write "journal check skipped
   (digest tool archived)".
4. **Event / timing risk** — earnings within ~10 trading days, catalysts, insider selling.
5. **One line:** "if I had to argue you out of this trade …"
6. **Verdict:** `kill` (a disqualifying flaw) · `caution` (only smaller / eyes open) ·
   `proceed` (no strong reason to kill — NOT an endorsement).

## Persist it onto the draft

Call **`annotate_intent(symbol, verdict, bear_case, side="BUY")`** so the bear case + verdict
land on the pending draft (JSON; no cockpit shows it any more — the web app was archived
2026-09-28). Handle the result:

- `annotated` → report it; the bear case is now on the draft.
- `IntentNotFound` → the draft expired / was consumed; still present the bear case in chat.
- `BadVerdict` → use exactly `kill` / `caution` / `proceed`.

## Present + hand off

Show the bear case + verdict in chat too. **It is advisory:** a `kill` is a strong flag, not an
action — placing the entry (or not) is still a separate, deliberate step via `trade-placer`
with the codeword. Never dismiss or place from this skill.

## Guardrails

- **Read-only. Never places, dismisses, or re-plans.** Only `annotate_intent` writes, and
  only the advisory bear case — the order the human confirms is unchanged.
- **Argue honestly.** Don't manufacture a scary bear case where the evidence is clean;
  "proceed" with a couple of watch-items is a valid, common outcome. The value is a *real*
  independent check, not theater.
- Bounded to the drafted PASSes (0–3/evening) — don't red-team the whole universe.
