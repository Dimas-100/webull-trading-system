# Webull Options Entry — scheduled entry runner

> **PAUSED since 2026-08-31** (red-team #2): `WEBULL_OPTIONS_ENTRY_PAUSED=1` in `.env`. The step still runs
> inside the suite and writes its run-log row, but opens nothing; the paper options book is winding down
> through the Paper-EOD Options exits and the real options sleeve is parked (2026-08-16). Everything below
> describes the runner when unpaused.

A Windows-scheduled, **zero-touch** paper runner that OPENS options positions on RSI(2) mean-reversion
signals. **100% paper — places no real orders.** Build 5 of the Monitor vision; closes the last
discretionary gap (options entries).

## What it does (weekday run, inside the ~5:30 PM ET suite, after the exit steps)

On the confirmed daily close, for each universe stock with **RSI(2) < 10** (the same signal as the
equity RSI2 runner) that isn't already held in options and is under the position cap, it opens a
**~ATM debit CALL vertical** (long ATM / short ~2 strikes higher, ~21–45 DTE) — codifying the owner's
`options_paper_experiments.md` practice. Defined risk (max loss = the debit paid), 1 contract, capped
at **$600 debit per unit**, **8 concurrent** option positions and **2.5% of the book in total open
debit at cost** (the dollar envelope usually binds first, around 5 units — see "Capacity" below).
Records one `data/activity/runs.jsonl` line (key
`options_entry`) + one `data/activity/actions.jsonl` "why" per open, lighting up the Monitor's
`options_entry` row. Exits are handled by the separate Paper-EOD Options runner.

Manual dry run: `.venv\Scripts\python.exe scripts\options_entry.py run` (add `--force` to bypass the
same-day guard). No oversold name → a clean `ok`/`no_op` line. A real signal WILL open a paper vertical.

## How it's scheduled

This runner has **no standalone Windows task** — it executes as part of the single **"Webull Paper
Suite"** task (weekdays ~5:30 PM ET, `run-paper-suite.bat` → `scripts/run_paper_suite.py`; see
`docs/paper-suite-runner.md`). **Verify:** a fresh `data\activity\runs.jsonl` line and a green
`options_entry` row on the Monitor after the suite runs.

## Requirements + exit codes

Needs the **OPRA** options entitlement (claimed) + the equity quotes entitlement (for RSI bars/spot).
Exit codes: `0` clean (opened something, or a valid no-op) · `1` hard fail (not entitled / token /
outage) · `2` ran but a per-name error occurred (see `logs\options_entry.log`).

## Vol + event gates (2026-07-25)

- **IV/HV richness filter:** ATM IV (from the already-fetched chain) ÷ 20-day realized vol
  must be ≤ 1.25, else the entry is skipped (`skipped_vol`). Missing IV/bars → the entry
  proceeds tagged `IV/HV n/a` (fail-open, visible).
- **Earnings guard:** a confirmed next-earnings date on or before the chosen expiry skips
  the entry (`skipped_earnings`); unknown proceeds tagged `earnings unknown`.
- Every gate outcome (vol skip, earnings skip, or fill) appends `{ts, symbol, spot, expiration, atm_iv, hv, ratio, verdict,
  action}` to `data/activity/iv_observations.jsonl` (seed data for future IV-rank) (a placement failure after the gates is not logged).
- Honest risk framing: the −50% exit stop is advisory at EOD cadence — the true per-trade
  risk is the full debit (capped by `max_debit`); the manager note reports the sleeve's
  total debit at risk nightly.

## Capacity (2026-07-29)

Two layers, whichever binds first — `max_positions` bounds the unit COUNT, `max_sleeve_debit_frac`
bounds the DOLLARS, so a run of expensive underlyings self-limits instead of multiplying risk:

- **8 units** ceiling · **2.5% of `starting_cash`** in open debit at cost (~$2,500 on the $100k paper
  book → ~5 concurrent units at a typical ~$500 debit). Mark-free by design: the gate sums position
  basis, never a live mark, so market data cannot fail the entry path.
- **Every blocked candidate is named** in the run row's `skipped`, and the row carries `signals` +
  `capacity_blocked` counts. `"no entry signals"` in a summary now means a genuinely quiet night;
  a capped night reads `0 opened [N capacity-blocked of M signal(s)]`. Before this, the loop
  `break`ed silently and 07-24/27/28 logged "no entry signals" while discarding 6 real signals.
- **`max_debit_frac_of_width` (0.65)** — an edge filter: a debit vertical breaks even at long strike +
  debit, so paying too large a share of the width pushes break-even out of reach. Rejects the −$700
  META shape (67% of a 20-wide) and MSFT/SMH at 68-70%. **Currently dominated** by the $600 dollar cap
  for every width above ~9.2 — it becomes load-bearing only if the per-unit budget is raised.
- **Reachability (snapshot at build time, then-20-name universe; 28 names since 2026-08-07):** 11 of 20
  names were tradeable at $600/unit; the 9 others needed $605-1,350 for an economically sound 2-wide.
  `BRK B` was unreachable for a separate reason — a space in its OCC root — fixed 2026-08-15 (OCC-root
  strip + strike-ladder probe). Raising the budget is an open owner decision; see the spec.

Rationale + the live measurement that kept the spread at 2-wide (a 1-wide pays ~2x the bid-ask
crossing cost as a share of width): `docs/superpowers/specs/2026-07-29-options-entry-frequency-design.md`.

## Tunable knobs (`OptionsEntryConfig` in `webull_web/options_entry_service.py`)

`dte_min/dte_max` (21/45), `width_strikes` (2), `contracts` (1), `max_debit` ($600), `max_positions`
(8), `max_debit_frac_of_width` (0.65), `max_sleeve_debit_frac` (0.025). Structure is fixed to debit
call verticals (defined risk); switch to singles / add puts by editing the service.
