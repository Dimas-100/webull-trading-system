---
name: strategy-red-team
description: >-
  Use for the monthly STRATEGY-level red-team (ledger `strategy-red-team-cadence`) —
  after the first Friday scorecard of each month, or on demand: "red-team the
  strategies," "argue against the book," "is the proof bar real?" Argues AGAINST each
  running strategy bucket and issues kill/caution/proceed verdicts. Strategy-level
  only — the bear case for a single candidate entry routes to `red-team` instead.
---

# Strategy red-team (argue against the book)

## What this is

The monthly judgment layer over the mechanical loop: one bounded bear case per
**running strategy bucket**, covering the four charter dimensions — assumptions,
regime dependence, expression costs, exit interplay — ending in a
kill/caution/proceed verdict per bucket. Gate-A/B and the scorecard measure;
this argues. Bounded: running buckets only (not the Lab, not retired strategies),
no balanced notes, no code changes. **Trigger:** the weekend after the FIRST Friday
scorecard of each month (the scorecard row is an input, not a prerequisite — run on
demand too), per the `strategy-red-team-cadence` ledger entry.

## Where things live

Run everything under `.venv/Scripts/python.exe` with the repo root on `sys.path`.
Modules: `webull_api/journal/{pairing,analytics}.py`, `webull_api/scorecard.py`,
`webull_web/decisions_store.py`. The full charter is the ledger entry
`strategy-red-team-cadence` in `data/activity/decisions.jsonl`. **Running buckets** =
the buckets the latest `scorecard_history.jsonl` row (or `scorecard.bucket_of` over
current closed trades) shows with live inventory or recent entries, plus the
real-money autopilot sleeve — enumerate from data, don't assume. Real-book check:
`webull_api.portfolio.get_balance/get_positions` per account (note:
`scripts/portfolio_summary.py` prints only the FIRST account). Base rates: only
RSI2 has an exact-config backtest today — a bucket without a base rate gets its
verdict anchored on realized risk vs its caps, stated as such.

## Step 1 — assemble the sample honestly (where passes go wrong)

**Never judge the headline proof-bar number.** It mixes legacy/discretionary rows
with strategy rows. Decompose first, with the production code, not by hand:

- Closed trades: `pair_fills(data/journal/fills.jsonl)` +
  `analytics.option_trade_to_closed(data/journal/option_trades.jsonl)`; exclude
  `analytics.is_coordination_artifact`; attribute buckets with
  `scorecard.index_actions(data/activity/actions.jsonl)` + `scorecard.bucket_of`.
- Split by era whenever a mechanics fix shipped mid-sample (compare entry time vs
  fix deploy time). Apply the ledger `proof-bar-read-scope` decision: the funding
  read = post-fix, strategy-attributed rows only.
- **Cross-check the journal against ledger truth:** `data/paper/default.json` /
  `options.json` (`realized_pnl`, positions) vs journal sums, and
  `data/activity/netliq_history.jsonl` as the honest book-level tape. Any
  divergence is an integrity finding (2026-07-28 found a missing −$700 options
  row and a phantom VZ lot this way).
- **Check the real book live** (read-only portfolio call) — never assume it from
  memory or ledger state.
- Runner summaries in `runs.jsonl` can mislabel (a slot-capped options night reads
  "no entry signals") — verify against the service code before citing one.

## Step 2 — base rates before verdicts

Judge live expectancy against the newest exact-config backtest
(`docs/reviews/*-rsi2-backtest.*`; rerun via `scripts/backtest_rsi2.py`, using
`--out-dir` elsewhere for experiments so the canonical report isn't clobbered),
and carry its selection-bias disclosure into every citation. The small-sample sin
is the failure mode this cadence exists to avoid: no verdict on a handful of rows
except relative to the base rate.

## Step 3 — verdicts

Same semantics as the per-trade `red-team`: **kill** = disqualifying flaw (propose
stopping the bucket — via ledger row, never by editing anything yourself) ·
**caution** = keep running bounded, no scale-up, name what evidence would resolve
it · **proceed** = no strong reason to kill; NOT an endorsement. Argue honestly —
a clean bucket gets "proceed" with watch-items, not a manufactured scare.

## Step 4 — outputs

1. The record: `docs/reviews/YYYY-MM-DD-strategy-red-team.md` (sample
   decomposition table, bear cases, verdicts, integrity findings).
2. A `decisions_store.append` ledger row ONLY where a verdict demands action.
3. One-line `docs/BUILD-LOG.md` entry; integrity findings → `docs/ROADMAP.md`.
4. Memory update if the program state changed.

## Guardrails

- Analysis + docs + ledger only. **No order-rail/gate/threshold/universe edits
  from this pass** — changes it motivates go through their own spec/sign-off.
- Bounded to running buckets; respect standing decisions (e.g. lab scope) rather
  than relitigating them.

Prior passes: 2026-07-28 (`docs/reviews/2026-07-28-strategy-red-team.md` — first
pass; rsi2-equity proceed, rsi2-options caution, autopilot caution, LLY drop).
