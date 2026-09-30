# Program map — the tracks, their books, and the day

> One page that answers "what is each track, where does it live, and what runs when." The tables
> between the `map:` markers are RENDERED from `webull_web/tracks.py` + `webull_web/routine.py`
> by `scripts/render_program_map.py` — edit the registry, not the tables; the drift test
> (`tests/test_program_map_render.py`) fails if they disagree. Prose outside the markers is hand-written.
> Spec: `docs/superpowers/specs/2026-09-07-program-map-and-today-design.md`.

## 1. The grid

Three tracks × money state. **Swing·RSI2** is the system the proof bar measures and the one every
real entry has followed; **Swing·Pullback** is the discretionary pullback plan behind the evening
scan and the autopilot's entry screen (both are first-class — owner, 2026-09-07); **Day·ORB** is the
opening-range-breakout trial, sandbox by day and a $400 real carve-out by hand. Lab is R&D with no
money. Options·Paper is parked.

<!-- map:grid:start -->
| Track | Paper | Real |
|---|---|---|
| Swing·RSI2 | PAUSED · RSI2 runner in the 17:30 suite; $100k simulator, $6k lots, 6 slots; the proof bar | RUNNING · rsi2_real queues RSI(2) < 10 entries in the 17:30 suite (ARMED 2026-09-14); the 09:31 autopilot places them at the open, rests 8% GTC stops and runs the exits |
| Swing·Pullback | — | RUNNING · evening-scan drafts → trade-placer (codeword); trade-planner; the autopilot entry screen |
<!-- map:grid:end -->

## 2. One section per cell

<!-- map:cells:start -->
### Swing·RSI2 · paper

| Field | Value |
|---|---|
| Plan doc | docs/rsi2-paper-runner.md |
| Book | $100k simulator · $6k per lot · 6 slots · $20k cash floor |
| Book source of truth | webull_api/strategy/rsi2.py Rsi2Config (dollars_per_signal, max_lots, cash_floor); data/paper/default.json starting_cash |
| Universe | 28 names — webull_api/strategy/rsi2.py UNIVERSE (widened 20→28 on 2026-08-07) |
| Entry | RSI(2) < 10 at the confirmed close → paper BUY queued for the next open by rsi2_service (17:30 suite) |
| Exit | RSI(2) > 70 → paper SELL queued for the next open; paper_eod holds RSI2 lots on trend-break |
| Schedule | 17:30 — Webull Paper Suite (run-log key rsi2) |
| Data | `data/paper/default.json` · `data/activity/rsi2_state.json` · `data/activity/runs.jsonl` |
| Journal | data/journal/fills.jsonl (paper-equity rows); scorecard bucket rsi2-equity |
| Kill switch | disable the 'Webull Paper Suite' task — no real money, no kill file |
| Review | Friday scorecard inside the suite; monthly red-team; proof bar 30/30 = Gate E (docs/proof-phase-charter.md §6) |

### Swing·RSI2 · real

| Field | Value |
|---|---|
| Plan doc | docs/superpowers/specs/2026-08-15-real-book-rsi2-path-design.md |
| Book | the Individual Cash account (the $400 day carve-out was retired 2026-09-21) |
| Book source of truth | broker balance (rsi2_real sizes from settled cash: a lot is net liq ÷ WEBULL_RSI2_REAL_SLOT_DIVISOR — 6 = the S6 rule — clipped under the autopilot cap, spec 2026-09-23; WEBULL_RSI2_REAL_DOLLARS is the older fixed figure and, if left set, one more ceiling; 5 lots via WEBULL_RSI2_REAL_MAX_LOTS); caps WEBULL_AUTOPILOT_MAX_NOTIONAL $525 + WEBULL_AUTOPILOT_MAX_POSITIONS 5 (.env, owner 2026-09-17) and codeword WEBULL_TRADE_MAX_NOTIONAL $500 — see the books-and-caps table |
| Universe | the same 28 names as the paper book (shared by design) |
| Entry | rsi2_real_service (17:30 suite; WEBULL_RSI2_REAL_ENABLED=true since 2026-09-14) queues one executable BUY per RSI(2) < 10 signal — QUEUE-ONLY, it never places; the 09:31 autopilot run turns the row into a next-open order behind gate.authorize (first system fills 2026-09-16). No hand entries on this sleeve |
| Exit | two-phase: an rsi2_above decision row → the 09:31 autopilot run cancels the resting stop and sells at the open (cancel-then-sell) |
| Schedule | 09:31 / 09:46 / 15:45 / 17:45 / 18:15 — Webull Autopilot (run-log key autopilot); 17:30 suite (rsi2_real row) |
| Data | `autopilot/state` · `autopilot/log` · `data/activity/executable_decisions.jsonl` · `data/exec/decisions.jsonl` |
| Journal | data/journal/fills.jsonl (real-equity rows); thesis rows in data/activity/decisions.jsonl |
| Kill switch | autopilot halt (kill file, phone-allowed) · WEBULL_AUTOPILOT_ENABLED |
| Review | proof bar + the Gate E arming checklist (docs/superpowers/specs/2026-08-07-arming-checklist.md); monthly red-team |

### Swing·Pullback · real

| Field | Value |
|---|---|
| Plan doc | playbook/swing-trading-plan.md |
| Book | $400 charter book for sizing the screen |
| Book source of truth | docs/proof-phase-charter.md §2 ($400); playbook/swing-trading-plan.md's worked BOOK = $500 is an example, not the live figure |
| Universe | webull_api/discovery.py CURATED_UNIVERSE (~180 names) plus injected movers; $10–$80 band (20% of book) |
| Entry | evening-scan skill → discover_and_draft → GTC buy-stop-limit drafts (data/intents/) → trade-placer with the codeword; the 17:45 autopilot runs the same screen behind its gate |
| Exit | manage_exits / exit-placer (stop −8%, target +20%, trend-break); resting protective stops via protect_positions |
| Schedule | 18:30 — evening scan (owner, Claude Desktop); 17:45 — autopilot entry screen |
| Data | `data/intents` · `data/plans` · `playbook/screens` |
| Journal | playbook/screens/<date>-eod-screen.md; fills in data/journal/fills.jsonl |
| Kill switch | the same autopilot kill file; drafts expire (30 min, 4 h for the routine) |
| Review | trade-review skill; plan review after 20 closed trades or 1 month (plan header) |
<!-- map:cells:end -->

## 3. Books and caps — every number in play, and which file wins

This table **records**; it changes nothing. Amend a plan document at its scheduled review, never here.

| Figure | Value | Where it lives | Status |
|---|---|---|---|
| Real account | broker balance | the Webull cash account via `webull_api.portfolio` | live |
| Charter real book | $400 | `docs/proof-phase-charter.md` §2 | the swing screen's sizing book |
| Swing plan `BOOK` | $500 (worked example) | `playbook/swing-trading-plan.md` §0 | example only — not the live figure |
| Day journal book `DAY_BOOK` | $400 | `webull_web/tracks.py` | historical denominator of the real hand journal and the cancelled ORB sandbox ledger (both paused) |
| Paper day book `DAY_PAPER_BOOK` | $25,000 (virtual, sandbox) | `webull_web/tracks.py` (owner 2026-09-22: wide-25k profile) | live in the sandbox; no cockpit cell yet |
| Real day carve-out `DAY_CARVE_OUT` | $0 | `webull_web/tracks.py` (the $400 hand book was retired 2026-09-21) | live |
| Day plan `DAYBOOK` | $150 | `playbook/day-trading-plan.md` §0 | **amendment pending** at the next scheduled review |
| Autopilot per-order cap | $525 | `WEBULL_AUTOPILOT_MAX_NOTIONAL` (`.env`, `webull_api/autopilot/config.py`) | owner-raised 2026-08-28; also clips the RSI2-real slot (net liq ÷ `WEBULL_RSI2_REAL_SLOT_DIVISOR`) — the evening note says "cap binds" when it is time to raise it |
| Codeword per-order cap | $500 | `WEBULL_TRADE_MAX_NOTIONAL` (unset — default `"500"` in `webull_trade_mcp/server.py`) | does not match the $525 above — owner reconcile item |
| Autonomous max positions | 5 | `WEBULL_AUTOPILOT_MAX_POSITIONS` (`.env`; owner 2026-09-17, was 1) | 0 when SPY is below its 200-SMA |
| Paper swing lots | $6,000 × 6 slots, $20k floor | `webull_api/strategy/rsi2.py` `Rsi2Config` | the proof-bar sizing |
| Paper day profile | `paper-400` | `webull_api/day_session/config.py` | sizes the sandbox like the $400 real carve-out |

Pending owner amendments (from the 2026-09-04 program review and the day-trial week-1 review):
the day plan's `DAYBOOK` $150 → $400; the day plan's entry order type (stop-limit through the
signal candle) and no-re-entry rule; the swing plan's `BOOK`; the $525 vs $500 cap mismatch.

## 4. Where things are

<!-- map:where:start -->
| Track | Money | Data | Journal | Kill switch |
|---|---|---|---|---|
| Swing·RSI2 | paper | `data/paper/default.json` · `data/activity/rsi2_state.json` · `data/activity/runs.jsonl` | data/journal/fills.jsonl (paper-equity rows); scorecard bucket rsi2-equity | disable the 'Webull Paper Suite' task — no real money, no kill file |
| Swing·RSI2 | real | `autopilot/state` · `autopilot/log` · `data/activity/executable_decisions.jsonl` · `data/exec/decisions.jsonl` | data/journal/fills.jsonl (real-equity rows); thesis rows in data/activity/decisions.jsonl | autopilot halt (kill file, phone-allowed) · WEBULL_AUTOPILOT_ENABLED |
| Swing·Pullback | real | `data/intents` · `data/plans` · `playbook/screens` | playbook/screens/<date>-eod-screen.md; fills in data/journal/fills.jsonl | the same autopilot kill file; drafts expire (30 min, 4 h for the routine) |
<!-- map:where:end -->

Shared: `data/activity/runs.jsonl` (every scheduled runner's row — the watchdog's evidence),
`data/activity/manager_notes.jsonl` (the nightly note), `data/activity/decisions.jsonl` (standing
decisions), `logs/` (SDK logs, pruned by `scripts/prune_logs.py`).

## 5. The day — what runs when (ET)

Automatic rows are Windows scheduled tasks; "you" rows are the owner's. The cockpit's Overview tab
shows this same list with live done / due / late status from the run log.

<!-- map:routine:start -->
| Time (ET) | Track | What | Who | Evidence | Late after |
|---|---|---|---|---|---|
| 09:30 | All | Market open — resting real orders fill | market | — | — |
| 09:31 | Swing·RSI2 | Autopilot morning trigger (queued exits) | scheduled task | `autopilot` | 20 min |
| 15:45 | Swing·RSI2 | Autopilot retry trigger | scheduled task | `autopilot` | 20 min |
| 16:00 | All | Market close | market | — | — |
| 17:30 | Swing·RSI2 | Evening suite (net-liq → RSI2-real → flows) | scheduled task | `rsi2_real` | 60 min |
| 17:45 | Swing·RSI2 | Autopilot evening run (decisions + entry screen; 18:15 backstop) | scheduled task | `autopilot` | 20 min |
| 18:25 | Swing·RSI2 | Nightly note (names today's orders, tomorrow's queue, stops) → phone | scheduled task | `note` | 30 min |
| 18:30 | Swing·Pullback | Evening scan (Claude Desktop, optional) → drafts → trade-placer (codeword) | you | `file:playbook/screens/{date}-eod-screen.md` | 0 min |
| 18:35 | Swing·RSI2 | Tiingo EOD backfill, free tier (today's EOD row lands after ~17:00; RSI2 research store) | scheduled task | `jsonl:data/tiingo/backfill_runs.jsonl` | 25 min |
| 19:00 | All | Suite watchdog | scheduled task | `watchdog` | 30 min |
| 21:00 | All | healthchecks.io deadline (external dead-man) | external | — | — |
| Friday | Swing·RSI2 | Friday: trade-review | you | — | — |
| last Friday | All | Monthly: strategy red-team | you | — | — |
<!-- map:routine:end -->

Weekend and NYSE-holiday days run nothing that matters: the suite reports "no fresh daily bar",
the paper day session refuses, and the watchdog looks back to the last weekday.
