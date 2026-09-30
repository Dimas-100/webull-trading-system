# Webull Paper-EOD Options — scheduled exit runner

A Windows-scheduled, **zero-touch** paper runner that auto-closes OPTION units on coded rules.
**100% paper — places no real orders.** Build 4 of the Monitor vision (the options sibling of the
equity Paper-EOD runner).

> **Sleeve status:** options ENTRIES have been paused since 2026-08-31 (`WEBULL_OPTIONS_ENTRY_PAUSED`), so this
> exit runner is winding the paper options book down; the real options sleeve is parked (2026-08-16).

## What it does (weekday run, inside the ~5:30 PM ET suite, after the equity Paper-EOD and Pool-shadow steps)

For **every** open option unit it applies the exit plan from `options_paper_experiments.md`, codified:
- **Take-profit:** unrealized ≥ **+50% of the entry debit**.
- **Stop:** unrealized ≤ **−50% of the entry debit** (a generic proxy for the discretionary
  underlying-swing-low invalidation — no per-unit level is stored).
- **Time-stop:** **≤ 7 DTE** (earliest leg expiration) — close before the worst late-expiry theta.

Debit/long units get all three; the time-stop applies to any structure. Closes are full-unit MARKET.
It records one `data/activity/runs.jsonl` line (key `paper_eod_options`) + one `data/activity/actions.jsonl`
"why" per close, lighting up the Monitor's `paper_eod_options` row. Options **entries** stay
discretionary (opened in-session; no mechanical options-entry strategy).

Manual dry run: `.venv\Scripts\python.exe scripts\paper_eod_options.py run` (add `--force` to bypass
the same-day guard). A flat options book → a clean `no-op` line (valid pass).

## How it's scheduled

This runner has **no standalone Windows task** — it executes as part of the single **"Webull Paper
Suite"** task (weekdays ~5:30 PM ET, `run-paper-suite.bat` → `scripts/run_paper_suite.py`; see
`docs/paper-suite-runner.md`). **Verify:** a fresh `data\activity\runs.jsonl` line and a green
`paper_eod_options` row on the Monitor after the suite runs.

## Exit codes

`0` clean (closed something, or a valid no-op) · `1` hard fail (market-data not entitled / token /
total marks outage) · `2` ran but a per-unit error occurred (see `logs\paper_eod_options.log`).
