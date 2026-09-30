# Webull Paper-EOD — scheduled exit runner

A Windows-scheduled, **zero-touch** paper runner that applies exit discipline to the paper equity book
and auto-closes signalled positions. Replaces the conversational Claude-Desktop paper-EOD check.
**100% paper — places no real orders.** Build 3 of the Monitor vision; the stop-loss the RSI2 runner
lacks.

## What it does (weekday run, inside the ~5:30 PM ET suite, right after the RSI2 step)

For **every** paper equity position, it applies the shared `exits.py` rules — **−8% stop**, **+20%
target**, **close-below-SMA20 trend-break** — and auto-places a full-close MARKET SELL on the paper
book for any that trip. It records one `data/activity/runs.jsonl` line + one `data/activity/actions.jsonl` "why"
per close, which light up the **Monitor** page (`paper_eod` row → `windows_task`).

Coordinates with the RSI2 runner via the account: if it stops out an RSI2 lot, RSI2's next-run
reconcile drops that lot. No shared state.

Manual dry run: `.venv\Scripts\python.exe scripts\paper_eod.py run` (add `--force` to bypass the
same-day guard). No positions or no signals → a clean `ok`/`no-op` line (a valid pass).

## How it's scheduled

This runner has **no standalone Windows task** — it executes as part of the single **"Webull Paper
Suite"** task (weekdays ~5:30 PM ET, `run-paper-suite.bat` → `scripts/run_paper_suite.py`; see
`docs/paper-suite-runner.md`). **Verify:** a fresh `data\activity\runs.jsonl` line and a green `ok`
`paper_eod` row on the Monitor page after the suite runs.

## Token / 2FA caveat + exit codes

Needs a valid Webull token in `.webull-tokens\`; on a 2FA lapse the run exits `1` (one-liner in
`logs\paper_eod.log`). Exit codes: `0` clean (closed something, or a valid no-op) · `1` hard fail
(market-data not entitled / token / total price outage) · `2` ran but a per-symbol error occurred.
