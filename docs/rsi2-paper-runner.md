# Webull RSI2 Paper — scheduled runner

A Windows-scheduled, code-backed paper runner for the RSI2 mean-reversion strategy. Replaces the old
Claude-Desktop-only routine. **100% paper — places no real orders.** Build 2 of the Monitor vision.

## What it does (weekday run, inside the ~5:30 PM ET suite)

1. Reconciles its ownership ledger (`data/activity/rsi2_state.json`) against the authoritative paper
   account (`data/paper/default.json`) — dropping any lot that never actually filled.
2. Computes RSI(2) over the 28-name universe (`webull_api/strategy/rsi2.py` `UNIVERSE`; 13 → 20 → 28, last widened 2026-08-07) on the confirmed daily close.
3. Exits owned lots with `RSI(2) > 70`; opens new lots with `RSI(2) < 10` (`$6,000`/signal, whole
   shares, max 6 lots, keep ≥ `$20,000` cash), placing MARKET on the paper book.
4. Records only verified fills to the ledger, writes one `data/activity/runs.jsonl` line + one
   `data/activity/actions.jsonl` "why" per order — which light up the **Monitor** page.

Manual dry run: `.venv\Scripts\python.exe scripts\rsi2_paper.py run` (add `--force` to bypass the
same-day guard). Off-hours it prints a `no-op` line (no fresh daily bar) — that is a valid result.

## How it's scheduled

This runner has **no standalone Windows task** — it executes as part of the single **"Webull Paper
Suite"** task (weekdays ~5:30 PM ET, `run-paper-suite.bat` → `scripts/run_paper_suite.py`; see
`docs/paper-suite-runner.md`). **Verify:** a fresh `data\activity\runs.jsonl` line and a green `ok`
`rsi2` row on the Monitor page after the suite runs.

## Token / 2FA caveat

The runner needs a valid (~15-day) Webull token in `.webull-tokens\`. If 2FA lapses, the bar/snapshot
reads raise and the run exits `1` (a clean one-liner in `logs\rsi2_paper.log`). Fix: run any read path
interactively once to trigger the SMS re-verification, then the scheduled job resumes.

## Exit codes (Task Scheduler "Last Run Result")

- `0` — ran clean (traded, or a valid no-op).
- `1` — hard fail (market-data not entitled, token/2FA lapse, network).
- `2` — ran but one or more per-symbol errors occurred (see `logs\rsi2_paper.log`).
