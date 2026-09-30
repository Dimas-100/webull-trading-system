# Real-book RSI2 path (Tier 3) — design

**Date:** 2026-08-15
**Status:** design approved (owner, 2026-08-15) — build complete, ship **OFF**, arm at the proof bar
**Depends on:** `2026-08-15-unattended-exit-correctness-design.md` (sleeve guard, cancel-then-sell,
`rsi2_above` trigger — all shipped 2026-08-15)

## Why

The strategy with the measured edge has no real-money path in either direction.
`webull_web/rsi2_service.py` line 1: *"RSI2 paper runner — 100% paper — no `trading` import, no
real-order path."* `grep rsi2 webull_api/autopilot/run.py` returns nothing. The autopilot's own
ENTRIES stage runs a different strategy (`discovery` + `swing_screen`), not RSI2.

Every real RSI2 entry to date was placed by hand through the `trade-placer` skill. The exit is now
automated (`rsi2_above`, queued 2026-08-15, id `d5bf6d639728400f9a63cee1c255eac5`); the entry is not.

## Non-goals

- Changing RSI2's entry/exit rules. Thresholds stay in `webull_api/strategy/rsi2.py` and are not
  touched. This is a plumbing change, not a strategy change.
- Arming anything. Ships behind a default-off flag; the owner arms it.
- Options. The real options sleeve is separately blocked — universe verticals run ~$490 against
  $40/$70 caps (`real-options-sleeve-cap-mismatch`, verified 2026-08-10).
- Migrating `copilot/agent.py` off the API key (tracked separately).

## Architecture: queue, don't place

`rsi2_real_service` computes signals and **queues executable decisions**. The autopilot's existing
DECISIONS stage places them. The real path adds **no new placement surface**.

```
rsi2_real_service  ──queues──>  executable_decisions.jsonl  ──>  autopilot DECISIONS stage
   (signals, sizing,                (untrusted queue)              gate.authorize -> caps ->
    settled-cash guard)                                            cooling -> replay refusal ->
                                                                   cancel-then-sell -> place
```

Reuses, unchanged and already security-reviewed: `gate.authorize`, per-order/positions/day caps,
the daily-loss halt, SPY-regime gating, cooling on risk-adding BUYs, the consumed-id replay token,
audit logging, and the cancel-then-sell path shipped 2026-08-15.

**Rejected — a new RSI2 stage inside `autopilot/run.py`.** It would create a second real-order code
path to secure and review, duplicating machinery that already exists and works.

**Why a separate module rather than extending `rsi2_service`:** that module's contract is "100%
paper, no real-order path." A paper runner that queues rows the autopilot will place breaks that
promise in substance even without importing `trading`. `runner_cli.py` makes the same promise for
the suite. Keep both true: new file, new suite entry, own flag.

## Components

### `webull_web/rsi2_real_service.py` (new)

Suite runner, run-log key `rsi2_real`, with the same lock + same-day guard + degrade-per-source
discipline as its paper sibling. Order in `PAPER_SUITE`: after `Net-liq` (needs the snapshot) and
before `Flows`.

First statement in `run()`: if `WEBULL_RSI2_REAL_ENABLED` is not truthy, return
`{"result": "no_op", "summary": "RSI2-real: disabled"}`. Nothing else executes.

Flow:
1. Read the real account (`INDIVIDUAL_CASH`, resolved as `autopilot/run.py:36` does — never
   `accounts[0]`, which is the **Crypto** account; that trap cost a full misread on 2026-08-15).
2. Read positions + balance; reconcile `rsi2_real_store` against broker truth.
3. Fetch RSI(2) for `universe ∪ owned` (reuse `rsi2_service._fresh_rsi`, count=30, same-day guard).
4. Call the pure `webull_api.strategy.rsi2.decide(...)` with a **real-book config** (below).
5. Filter entries through the affordability + settlement guards.
6. Queue each surviving decision via `decisions_exec.append`.

### `webull_web/rsi2_real_store.py` (new)

Mirrors `rsi2_store`'s shape (`owned_lots`, `pending_orders`, `reconciliation_log`) against the real
account, under its own path so the paper ledger is never co-mingled. Written **only** after a fill
is verified at the broker — the discipline the paper ledger already enforces ("a logged intent is
not a fill"), which caught four phantom lots on 2026-07-24.

**This closes an open gap.** The sleeve guard shipped 2026-08-15 holds RSI2 lots only because they
have *no* plan — absence of evidence. With this ledger they are **positively** attributed, and
`_is_attributed_swing` can be complemented by a positive `_is_rsi2_lot` check.

### Real-book config

The paper config is calibrated to a $100k book and must not be reused:

| Field | Paper | Real (initial) |
|---|---|---|
| `dollars_per_signal` | 6000.0 | `WEBULL_RSI2_REAL_DOLLARS` (default: all settled cash) — since 2026-09-23 one ceiling among `min(net liq ÷ divisor, cap, DOLLARS)` when the divisor is set |
| slot divisor | — | `WEBULL_RSI2_REAL_SLOT_DIVISOR` (unset: off; `6` = the S6 rule; spec `2026-09-23-rsi2-real-equity-sizing-design.md`) |
| `max_lots` | 6 | `WEBULL_RSI2_REAL_MAX_LOTS` (default 1) |
| `cash_floor` | 20000.0 | 0.0 |

Env-driven so the sleeve scales with funding — reaching ~$1.5k must not require a code change.
Entry/exit thresholds (`entry_below=10`, `exit_above=70`) are shared with paper and unchanged.

## Sizing, settlement, and the whole-share constraint

**Size from `settled_cash`, never `buying_power` or `cash_balance`.** The broker exposes it directly
(verified live 2026-08-15):

```json
"cash_balance": "300.00", "settled_cash": "300.00", "unsettled_cash": "0.00", "buying_power": "300.00"
```

This eliminates Good-Faith-Violation exposure *structurally* rather than by ACH-date arithmetic: if
every buy is settled-funded, every subsequent sale is automatically safe. No date guessing, no
5-day timer, no dependence on when a deposit posts.

**Whole shares only.** A fractional position cannot carry a resting protective stop — the broker
rejects it (verified 2026-07-22) — and fractional sells are core-hours-only, which made the entire
fractional protect/exit layer inert at the 5:45 PM run (live finding 2026-07-31). A real lot that
cannot be protected must not be opened. `quantity = floor(dollars / price)`; if that is 0, skip and
log the skip.

**Affordable sub-universe.** At $300 settled cash, names above ~$300 are unreachable whole-share.
This is a *funding* constraint, not a signal constraint: the runner must log skipped signals as
`unaffordable` with the price, so the proof-bar read never mistakes "couldn't afford it" for "no
signal" — the same distinction that made `options-entry-capacity` a false scarcity story in July.

## Exit path

Exits reuse what shipped 2026-08-15: for each owned real lot, ensure exactly one queued
`rsi2_above` decision exists (`qty: ALL`, threshold from config). Idempotent — never queue a second
row for a lot that already has a live one, and mark rows for lots no longer held as `cancelled`.

The autopilot's DECISIONS stage places it, clearing any resting protective stop first via
cancel-then-sell. The full loop is then unattended end to end.

## Cadence — and why the 09:35 autopilot trigger is a dependency, not a nicety

`AutopilotConfig.cooling_minutes` defaults to **120** and is not overridden in `.env`. Risk-adding
BUYs age from the executor's own `first_seen` stamp, so a row queued by the 17:30 suite is 15
minutes old at the 17:45 autopilot run and **cannot place that evening**. That veto window is a
deliberate safety wall and must not be shortened for this feature.

Today the autopilot has only two Task Scheduler triggers, 15:45 and 17:45. So without a change, a
BUY queued Monday evening first becomes placeable at **Tuesday 15:45 — near the close.**

That breaks fidelity with the paper sleeve. `rsi2_service` queues *next-open* orders, and the
proof-bar comparison assumes real and paper fill on the same convention. Systematically filling the
real book near the close while paper fills at the open injects a bias into the exact figures the
go-live decision rests on.

**Resolution:** add a 09:35 ET trigger to the `Webull Autopilot` task. `WEBULL_AUTOPILOT_WINDOWS`
already authorizes `09:35-15:59`, so the gate needs no change — only the scheduler entry. A row
queued at 17:30 then cools overnight (≈16h) and places minutes after the next open, matching the
paper convention.

```powershell
$t = Get-ScheduledTask -TaskName 'Webull Autopilot'
Set-ScheduledTask -TaskName 'Webull Autopilot' `
  -Trigger @($t.Triggers + (New-ScheduledTaskTrigger -Daily -At 9:35AM))
```

Owner action (the assistant is harness-blocked from registering autopilot tasks). Independently
useful — it is also what closes the morning window in which a fresh position carries no resting
stop — but for this feature it is a **precondition for arming**, and belongs in the arming
checklist below.

## Testing

- Disabled by default: with the flag unset, `run()` touches nothing — no broker read, no queue write.
- Sizing: `settled_cash` is the basis; a book where `cash_balance > settled_cash` sizes from the
  smaller figure. Explicit regression against using `buying_power`.
- Whole-share: price above the budget → 0 shares → skipped and logged `unaffordable`, never queued.
- Settlement: `unsettled_cash > 0` does not inflate the budget.
- Ledger: a queued row is not a lot; only a broker-verified fill writes `owned_lots`.
- Exit idempotency: two runs over one held lot produce exactly one queued `rsi2_above` row.
- Account resolution: an account list whose first entry is `CRYPTO` still selects
  `INDIVIDUAL_CASH`.
- Attribution: a real RSI2 lot is positively identified, and the sleeve guard still holds it on a
  break-only signal.
- The suite's no-placement guard test must still pass for every module it covers.

## Invariants

No new placement path. `safety.should_submit` untouched. `gate.authorize` untouched. This runner
can only ever *write a queue row*; every wall between a queue row and a real fill stays exactly
where it is. The queue file remains untrusted — `autopilot/state` stays the trust root, and the
DECISIONS stage's existing replay/cooling/caps logic is what makes a queued row safe.

## Arming (owner, not assistant)

1. Proof bar 30/30 (28/30 as of 2026-08-14) — standing decision `gate-e-parked-stay-paper`.
2. Funding ~$1.5k (owner target, amended 2026-08-03).
3. **09:35 autopilot trigger registered** (see Cadence) — without it, real entries fill near the
   close while paper fills at the open, biasing the very comparison the go-live rests on.
4. `/security-review` of this build.
5. Owner sets `WEBULL_RSI2_REAL_ENABLED=true`.

The assistant cannot perform steps 3 or 5; the harness blocks autopilot task registration and
autopilot/trade env changes.

## Open question for the owner (does not block the build)

At `max_lots=1` the real sleeve takes the *first* qualifying signal each evening, which on a
multi-signal night is an arbitrary pick. Options: lowest RSI(2) (deepest dip), cheapest affordable
(most shares), or the ledger's least-recently-traded name. Defaulting to **lowest RSI(2)** as the
most strategy-consistent tie-break; revisit when `max_lots > 1` makes it moot.
