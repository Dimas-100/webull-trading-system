---
name: status-checkin
description: >-
  Use when the owner asks how the managed book is doing — "morning check-in,"
  "evening check-in," "how did the paper suite do," "what's open in the paper
  books," "how's the proof phase going," "anything I need to look at" — from a
  phone session or any session. Strictly read-only: it reports from files
  already on disk. NOT for running the scan (evening-scan), reviewing closed
  trades (trade-review), or placing/drafting anything.
---

# Status Check-in (the phone-sized digest)

## What this is

The morning/evening "how are we doing" digest for the Claude-managed account,
built **entirely from files the nightly jobs already wrote**. It exists so a
phone session answers in seconds from ~5 reads instead of exploring the repo —
and so the answer never involves running anything.

**Read-only, always.** This skill never runs the paper suite, any `.bat` /
scheduled task / autopilot / lab command, never starts the web app, and never
places or drafts an order. If the data is stale, **report the staleness** — the
fix (running the suite, re-authing the token) happens at the desktop.

## Source-of-truth map (read in this order)

| Question | Source | Notes |
|---|---|---|
| How did the suite / runners do? | last line of `data/activity/manager_notes.jsonl` | The composed nightly note — runner results, book net-liqs + deltas, protection, autopilot posture, proof bar, lab funnel. **This is the digest; relay it nearly verbatim.** |
| Runner health, if the note is missing/stale or reports errors | tail of `data/activity/runs.jsonl`; error detail in tail of `logs/paper_suite.log` | Per-runner `ok`/`error` rows; the log has tracebacks/429s. |
| Open paper positions & orders | `data/paper/default.json` (equity) · `data/paper/options.json` (options) | Positions, open orders, cash, realized P&L. |
| Newest evening screen | newest `playbook/screens/*-eod-screen.md` | Report its Outcome + "Action for Dimas" lines. |
| What the proof phase says to watch | the note's "Proof bar" line, judged against `docs/proof-phase-charter.md` §6 | 30+ decisions · positive expectancy · clean discipline. Under ~30 decisions, expectancy is noise — say so. |

## Staleness discipline (the check that makes this trustworthy)

- The suite runs **weekdays 5:30 PM ET**. If the newest manager note is older
  than the last completed weekday, the suite didn't run (PC asleep, token
  lapsed) — lead with that, then check `runs.jsonl` for the last real run.
- If the newest screen is older than the newest manager note, say the
  evening-scan writeup hasn't been produced since that date — don't present an
  old screen as current.
- Every dollar figure is **as of the note's timestamp**, not live. Say so once.

## Output shape

Four short blocks, then stop:

1. **Suite** — headline from the manager note: what ran, fills, book deltas.
2. **Positions** — open paper equity + options positions (and any open orders).
3. **Screen** — newest EOD screen's outcome + action line (with its date).
4. **Watch** — proof-bar progress, autopilot posture, protection gaps, and any
   staleness flags. If something needs a hand (e.g. a stop that won't rest, a
   lapsed token), name it as a **desktop action** — don't attempt it here.

## What this does not do

- Run or restart anything, ever — no suite, no `.bat`, no web app, no
  `schtasks`, no autopilot commands (the emergency **halt** lives outside this
  skill; see the operator guide's "Phone" section).
- Place, draft, cancel, or modify orders; touch `.env`; edit plans or skills.
- Pull live quotes or fresh market data — this digest is a faithful relay of
  what the nightly jobs recorded, not a new scan (that's `evening-scan`, at the
  desktop).
