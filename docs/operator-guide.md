# Operator's Guide — Claude / Webull toolkit

> The web app, webull-analysis and snaptrade-portfolio were archived 2026-09-28 (docs/ARCHIVE.md); kestrel is
> the dashboard.

This maps **what I want to do → the exact trigger** — so I never have to memorize tool names, phrases, or commands.
To regenerate this file, use the prompt in **[Regenerate this guide](#regenerate-this-guide)** at the bottom.

**Legend:** 🟢 read-only · 🧪 paper/sim · 🔴 real-order (needs a human gate) · ⚙️ infra

**By track** (names from `docs/program-map.md`):

| Track | Paper | Real | Map |
|---|---|---|---|
| Swing·RSI2 | [paper suite, 17:30](#terminal--scripts) | [Trading autopilot](#the-trading-autopilot-autonomous-real-money-placement-) · `webull-trade` codeword row | [§2 cells](docs/program-map.md#2-one-section-per-cell) |
| Swing·Pullback | — | [webull-trade drafts](#claude-desktop-chat) · the `evening-scan` skill | [§2 cells](docs/program-map.md#2-one-section-per-cell) |
| Swing·IBS ETF | the 15:56 IBS ETF book row under [Terminal](#terminal--scripts) | — (paper only; real money needs its own arming spec) | [§2 cells](docs/program-map.md#2-one-section-per-cell) |
| Day·ORB | the 09:25 paper day session row under [Terminal](#terminal--scripts) | the MORNING ASSIST row under [Terminal](#terminal--scripts) · [Phone](#phone-claude-code-mobile--cloud-sessions) for the journal | [§2 cells](docs/program-map.md#2-one-section-per-cell) |
| Lab | [Lab autopilot](#the-lab-autopilot-strategy-rd--token-free-places-nothing) | — | [§2 cells](docs/program-map.md#2-one-section-per-cell) |
| Options·Paper | PARKED (entries paused 08-31) | PARKED | [§3 books and caps](docs/program-map.md#3-books-and-caps--every-number-in-play-and-which-file-wins) |

Full per-track fields, books and caps, where things are, and the day's timeline: `docs/program-map.md`.

---

## Claude Desktop (chat)

Talk to these connectors in plain English. Restart Claude Desktop after loading/updating a connector. Everything here is read-only or simulated **except** `webull-trade`'s `place_order` and the official `webull` connector.

### webull-lab — the Strategy Learning Machine

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Get token-free context (universe, regime, objective, seed rules, quota, patterns to avoid) before proposing | "Give me the lab brief so I know what strategies to propose." | webull-lab | JSON proposal context | Connector loaded |
| 🧪 Submit candidate strategies for the instant Gate-A walk-forward screen | "Propose these strategies to the lab and run the Gate-A screen." | webull-lab | JSON: accepted ids, gate_a_failed, dups, new trial count | Connector loaded; token valid + market-data entitlement |
| 🧪 Open forward-proving trials for Gate-A passers | "Submit these candidate ids to start proving trials." | webull-lab | JSON: submitted + skipped ids | Connector loaded; a candidate id; token valid |
| 🧪 Advance all proving trials on new bars, promote/continue/kill (the heartbeat) | "Advance the lab's proving trials." | webull-lab | JSON: advanced / promoted / killed (no-op if no bar closed) | Connector loaded; token + entitlement |
| 🟢 List library candidates ranked by consistency (filter by status) | "Show me the lab library, only the proving ones." | webull-lab | JSON ranked list | Connector loaded |
| 🟢 Full detail for one candidate (rule, Gate-A report, lineage, trial book) | "Show me the full detail for candidate `<id>`." | webull-lab | JSON candidate detail | Connector loaded; a candidate id |
| 🟢 Graduate confirmed-proven candidates to the proven shelf | "Promote these confirmed-proven candidates to the proven shelf." | webull-lab | JSON; refuses non-confirmed_proven; places nothing | Connector loaded; candidate must be confirmed_proven |
| 🟢 Archive (reject) candidates you don't want | "Reject candidates `<id1>`, `<id2>` — reason: overfit." | webull-lab | JSON result | Connector loaded; candidate ids |
| 🟢 One-glance progress snapshot of the whole lab | "How's the lab doing?" | webull-lab | JSON: M tested, cycles, library by status, proven shelf | Connector loaded |

### webull-trade — draft/place orders from chat

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🧪 Draft a single equity/ETF order (does NOT place) | "Draft a buy of 2 shares of AAPL as a GTC limit at 180 — thesis: pullback entry." | webull-trade | JSON draft+preview | Connector loaded; token valid |
| 🧪 Screen a watchlist and draft every swing PASS as a buy-stop-limit | "Screen my watchlist and draft the passes." | webull-trade | JSON {drafted, skipped, errors} | Connector loaded; token + entitlement |
| 🧪 Scan open positions and draft a full-close SELL for each exit signal | "Check my positions for exits and draft the sells." | webull-trade | JSON {drafted, held, errors} | Connector loaded; token + entitlement |
| 🧪 Full pre-market routine in one call (screen entries + scan exits, longer TTL) | "Call morning_routine and summarize what was drafted." | webull-trade | JSON {entries, exits, message}; drafts held 4h TTL | Connector loaded; token valid |
| 🧪 The nightly evening scan (movers → discover+draft → exits → protective stops → red-team → **writeup saved to `playbook/screens/`**) | "Run the evening scan." | webull-trade + official webull (movers feed) | Drafts (JSON) + the saved EOD screen writeup | Connector loaded; token + entitlement. **Manual since the Desktop automation was cancelled (2026-07-17)** — say it each evening or it doesn't run |
| 🔴 DIRECTLY place a REAL small equity/ETF order — no web confirmation | "Place a real order: buy 1 share of PLUG limit 2.50 GTC, codeword `<your-secret>`." | webull-trade | LIVE order at Webull + JSON {placed, notional, result} | `WEBULL_TRADE_CODEWORD` set in .env (you type it); within `WEBULL_TRADE_MAX_NOTIONAL` ($500 default); token valid |
| 🔴 Run ONE **autonomous** cycle — places REAL orders within caps, NO codeword (gated by enable+kill+caps+window) | "Run the autopilot." → `autopilot_run` | webull-trade | JSON {placed, skipped, errors} + LIVE orders (only if enabled + in window) | `WEBULL_AUTOPILOT_ENABLED=true`; inside `WEBULL_AUTOPILOT_WINDOWS`; kill-file absent; token + entitlement. See "The Trading autopilot" below |

### webull-options — options market data & analytics (read-only)

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 List option expirations + spot | "What option expirations are available for AAPL?" | webull-options | ISO expiration dates + spot | OPRA options entitlement |
| 🟢 Option chain for an expiration (call/put: last, bid/ask, IV, greeks, OI, vol) | "Pull the AAPL option chain for 2026-07-17 (12 strikes each side of ATM)." | webull-options | Paired call/put chain | OPRA; expiration YYYY-MM-DD |
| 🟢 Quote specific contracts by OCC symbol | "Quote these options: AAPL260717C00295000, AAPL260717P00300000." | webull-options | Per-contract quotes | OPRA; comma-sep OCC (≤20) |
| 🟢 Expected move (1σ range/%), ATM IV, best-effort IV-vs-HV | "What's the expected move for AAPL by 2026-07-17?" | webull-options | Expected move + ATM IV | OPRA options entitlement |
| 🟢 Analyze a single/vertical strategy (BE, POP, max P/L, greeks, EV, PoT, scenario) | "Analyze buying the AAPL 2026-07-17 295 call." | webull-options | Full strategy analytics | OPRA; legs by OCC symbol |
| 🟢 IV skew (across strikes) + term structure (ATM IV across expirations) | "Show me the vol surface / IV skew for AAPL." | webull-options | Skew + term structure | OPRA options entitlement |
| 🟢 Covered-call / cash-secured-put annualized yields per strike | "Show covered-call income yields for AAPL 2026-07-17." | webull-options | Per-strike annualized yields | OPRA; side=call/put |
| 🟢 Implied vs historical earnings move (expensive/cheap/fair) | "Is the options-implied earnings move for AAPL expensive or cheap?" | webull-options | Implied move + verdict | OPRA (+ Finnhub for history) |

### webull-paper — simulated trading (no real money)

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🧪 See simulated equity account (cash, buying power, positions, orders, history) | "Show me my paper trading account." | webull-paper | JSON account view (resting LIMITs re-evaluated on this call) | Connector loaded |
| 🧪 Place a simulated equity paper order | "Paper trade: buy 10 shares of AAPL at market." / "LIMIT buy 5 TSLA at 200 GTC." | webull-paper | Updated account; fill written to Journal | Connector loaded |
| 🧪 Cancel an open simulated equity paper order | "Cancel my paper order `<id>`." | webull-paper | Updated account view | Connector loaded; an open order id |
| 🧪 See simulated OPTIONS paper account (units, marks, P&L, collateral, orders) | "Show me my options paper account." | webull-paper | JSON options view (auto-settles expirations) | Connector loaded |
| 🧪 Place a simulated options paper order (single leg or vertical) | "Paper trade options: buy to open the AAPL 300 call expiring 2026-07-17." / "close options unit `<unit_id>`." | webull-paper | Updated options view; filled CLOSE is journaled | Connector loaded; OCC symbols |
| 🧪 Cancel an open simulated options paper order | "Cancel my options paper order `<id>`." | webull-paper | Updated options view | Connector loaded; an open order id |

### webull-strategy — backtest & save strategies (read/additive-write)

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Backtest a strategy you describe in plain English | "Backtest an SMA 20/50 crossover on SPY, 1D, 300 bars." | webull-strategy | Metrics (vs buy&hold, win rate, expectancy, drawdown) + trade log | Market-data entitlement |
| 🟢 Backtest a saved strategy by id | "Backtest my saved strategy `<id>`." | webull-strategy | Same metrics + trade log | A saved strategy id |
| 🟢 Save a strategy | "Save this strategy." (re-saving the same name overwrites) | webull-strategy | Saved strategy + id; additive write only | Connector loaded |
| 🟢 List your saved strategies | "List my saved strategies." | webull-strategy | List (id, name, symbol…) | Connector loaded |
| 🟢 Full definition of one saved strategy | "Show me the details of strategy `<id>`." | webull-strategy | Full JSON | A saved strategy id |

### webull (official vendor connector)

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🔴 Real-money order/account tools via the vendor package | Use the official `webull` connector's own order tools (vendor `webull-openapi-mcp`) | webull (official) | Real Webull order/account actions | Official connector loaded; live creds — real money. Prefer `webull-trade`'s codeword `place_order` (one of the three authorized real-order exceptions) |

---

## Phone (Claude Code mobile / cloud sessions)

Claude Code sessions sync to the phone and run in the cloud; they can reach these repo files
**only while the desktop app is open and connected**. **Posture — read + journal + review
only.** A phone session reads anything, appends journal entries, and reviews the book; it
never places, drafts, arms, or reconfigures anything.

### What the phone receives (ntfy, one topic: `WEBULL_MANAGER_NOTE_NTFY`; restructured 2026-09-21)

Plain text only (the ntfy phone apps render no Markdown) and every body is held under ntfy's
4,096-byte threshold — over it the phone gets a `message.txt` attachment instead of the note.
The emoji in front of a title is the state at a glance: ✅ nothing needs you · ⚠️ read the
ATTENTION section · 🚨 the suite/autopilot went dark · 💸 real money moved · 🔬 research ·
🎯 a live ticket.

| Push (title) | When | Priority · tag | What is in it |
|---|---|---|---|
| **Nightly note - DATE** | every weekday ~18:25 ("Webull Nightly Note" task, after the 18:15 autopilot backstop) | `default` ✅, or `high` ⚠️ whenever ATTENTION is non-empty | Header: REAL net-liq + today's delta **ex owner flows** (a deposit is shown as `flow +$…`), autopilot posture + today's placed/skipped. Then TODAY (each real order the autopilot placed, named: side, qty, symbol, limit, entry/exit/stop placed, time), TOMORROW (queued executable decisions: `BUY IWM 1 lmt 281.81 at open, expires …`, armed RSI(2) exits with the last-read RSI), STOPS (every held real lot with its resting stop), MARKET (SPY regime, earnings on held names), SYSTEM (suite N/2 reported, RSI2-real, exec slippage). Last: **ATTENTION** — **NO STOP** on a held real lot, a failed autopilot place, runner errors, TRIGGERED standing decisions, earnings on a REAL lot, unverified protection, unreadable sources. Parked paper systems are no longer in the note (2026-09-29). Over 2,500 bytes → TODAY folded to 3, then SYSTEM/MARKET dropped with a `[trimmed: …]` tail; ATTENTION is never trimmed. |
| **Autopilot HH:MM - N placed[, E error(s)]** | only after a run that PLACED a real order or hit an error (any of the 09:31/09:46/15:45/17:45/18:15 runs); silent runs send nothing | `default` 💸 · `high` ⚠️ on errors | One line per placed order (`- BUY 1 AXP @ 286.25 (entry)`), an ERRORS list, `skipped N \| enabled=… kill=…` |
| **Suite watchdog - DEAD / DEGRADED / AUTOPILOT SILENT / BACK-ALERT** | 19:00 weekdays (or boot catch-up) when the suite or the 5:45 PM autopilot never reported | `high` 🚨 | The verdict + the missing runner keys |
| **Suite watchdog - ADVISORY** | 19:00 when the suite is healthy but the previous weekday has no bench-feeder row | `low` ℹ️ | Which feeder night never reported |
| **Bench feeder - DATE** | ~18:40 only when the nightly develop batch produced a pass or errored | `low` 🔬 · `high` ⚠️ on error | The digest head as plain text: counts, PASSES, CONFIRMS OPENED TONIGHT, STAGED TO PAPER |
| **Session grid - SYMBOL** | 09:25–09:50 on a day the paper session-grid screen cuts a ticket | `high` 🎯 | The three-line ticket (+ a late warning when the limit is stale) |

The stored note (`data/activity/manager_notes.jsonl`, one row per ET day) keeps the full evening
note (`lines`, one sentence per source); the phone body is a different rendering of the same
gather, never a different source of truth.

| Goal | Say | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Morning/evening digest of the managed book (last suite run, book net-liqs, open paper positions, newest EOD screen, proof bar, autopilot posture) | "Morning check-in." / "Evening check-in." / "How did the paper suite do?" | phone session → `playbook/skills/status-checkin` | Read-only digest with staleness flags | Desktop app open + connected |
| 🟢 Log a fill into the trading journal seconds after it happens | "Log: bought 4 SOFI @ 16.20 — plan was the swing setup from Tuesday's screen." | phone session → `playbook/skills/journal-capture` | A template-true entry appended to `playbook/journal.md` (open on entry; completed in place on the closing fill) | Desktop app open + connected |
| 🟢 Weekly tally / "how am I doing" from the phone | "Review my trades." / "What's my win rate / expectancy?" | phone session → `playbook/skills/trade-review` | §9 metrics digest, plan-followed % first | Desktop app open + connected |
| 🛑 EMERGENCY halt of the trading autopilot — the ONE allowed state change (fail-closed, risk-reducing only) | "Halt the autopilot." → the session creates the kill-file (`autopilot halt` semantics) | phone session | Autopilot places nothing until resumed at the desktop | Desktop app connected (or create the KILL file yourself via a synced folder) |

**Never from a phone session** (these stay desktop/owner-gated; this list weakens nothing):

- `trade-placer` / `exit-placer` / the codeword `place_order` tool — no placement, no "just this once."
- `autopilot_run`, `autopilot resume`, or ANY autopilot/trade env change (`WEBULL_AUTOPILOT_*`, `WEBULL_TRADE_*`, `WEBULL_DAYTRADE_*`, `.env` edits). **Halt is the only allowed direction.**
- `start_session` (the day-trade session codeword) — starting an autonomous real-money session is a desktop gesture; from the phone only `session_status` and `halt_session` (freeze) are allowed.
- Editing the trading plans (`playbook/*-trading-plan.md`), the charter, or playbook skills — rule changes happen only through a due `trade-review` system review, at the desktop.
- Drafting orders "to review later" — draft surfaces are Desktop territory; from the phone, capture the intent in the journal narrative (or in the check-in follow-ups) and let the desktop evening-scan handle it.

The submit gates (`safety.should_submit`, dry-run default, codeword, autopilot gate) are
byte-untouched by this surface — the phone posture is a usage rule layered on top, not a new
code gate.

---

## Terminal / scripts

Run from the repo root `C:\path\to\webull-trading-system` (double-click the `.bat`, or run the shown command).

| Goal | Run | Where | Output | Prereqs |
|---|---|---|---|---|
| ⚙️ Print a lab progress snapshot | `lab-status.bat` (or `lab-status.bat 20`; or `.venv\Scripts\python.exe scripts\lab_status.py`) | terminal | Text: M tested, cycle history, library, proven shelf | none (no network, no orders) |
| 🧪 Run the WHOLE paper suite now (every code-backed paper runner — list in `webull_web/runner_cli.py`) | `run-paper-suite.bat` (or `.venv\Scripts\python.exe scripts\run_paper_suite.py`) — **SCHEDULED** weekdays 5:30 PM ET as the "Webull Paper Suite" task (wakes the PC from sleep) | terminal / Task Scheduler | Each runner's summary | Valid Webull token; paper book auto-runs daily. Manage: `schtasks /run\|/change /disable\|/delete /tn "Webull Paper Suite"`. Docs: `docs/{paper-suite-runner,host-setup}.md` |
| 🟢 Send the nightly note now | `run-nightly-note.bat` (or `.venv\Scripts\python.exe scripts
ightly_note.py [--force]`) — **SCHEDULED** weekdays 18:25 as the "Webull Nightly Note" task (no wake; after the 18:15 autopilot backstop). Before 16:00 ET it no-ops without stamping unless `--force` | terminal / Task Scheduler | `datactivity\manager_notes.jsonl` row + ntfy push + run-log `note` | `WEBULL_MANAGER_NOTE_NTFY` for the push |
| 🟢 Dead-man watchdog for the paper suite + the 5:45 PM real-money autopilot | `.venv\Scripts\python.exe scripts\suite_watchdog.py` (`--force` to ignore the weekday/window guards) — **SCHEDULED** weekdays 7:00 PM ET as the "Webull Suite Watchdog" task (separate task, `StartWhenAvailable`: catches up at next boot if the PC was off) | terminal / Task Scheduler | Silence = healthy. ntfy push on `dead` (suite never reported), `degraded` (a runner missing), or `autopilot-silent` (the 5:45 PM run never reported), plus a lower-key `ADVISORY` push when the PREVIOUS weekday has no `bench_feeder` row (the 17:35 feeder never ran or was killed — check `dataencheeder\<date>.md` and the task's last result; the suite verdict and exit code are untouched); a pre-window/weekend/boot-time firing instead looks back and back-alerts the most recent weekday evening left uncovered (consecutive outages each alert). Note: DISABLING the "Webull Autopilot" scheduled task (unlike setting `WEBULL_AUTOPILOT_ENABLED=false`, which still runs and still heartbeats "disabled") stops the heartbeat row entirely, so the watchdog will push `autopilot-silent` every weekday evening — a deliberate pause of that task should expect it | Task registered (`scripts\register-watchdog-task.ps1`); `WEBULL_MANAGER_NOTE_NTFY` set for the push |
| 🟢 Backfill / refresh 30-yr Tiingo daily history (offline store for the Lab + backtests) | `run-tiingo-backfill.bat` (or `.venv\Scripts\python.exe scripts\tiingo_backfill.py`; `--symbols A,B` · `--all-curated` · `--in-band` · `--refresh` · `--dry-run`) — **SCHEDULED** weekdays 6:35 PM ET as the \"Webull Tiingo Backfill\" task (now `--all-curated --in-band`, then `scripts\day_trade_universe.py`), 30 min ahead of the 7:05 PM Lab cycle (which reads `WEBULL_LAB_BAR_SOURCE=tiingo` / `WEBULL_LAB_LOOKBACK_BARS=1900` from `.env`) | terminal / Task Scheduler | one line per symbol + `data\tiingo\_manifest.json`; exit 1 only if nothing fetched | `TIINGO_API_TOKEN` in .env (owner); never touches the trading path |
| 🟢 Build the nightly AUTO day-trade universe (band + 20-day ADV off the broad Tiingo daily store; the morning screen/paper session read this file, never Tiingo itself) | `.venv\Scripts\python.exe scripts\day_trade_universe.py [--out data\day_trade\universe-auto.txt] [--profile plan-v1] [--max-stale-days 4]` — runs at the tail of `run-tiingo-backfill.bat`, after `--in-band` | terminal / Task Scheduler | `data\day_trade\universe-auto.txt` (2-line header + sorted symbols); refuses (exit 1, old file untouched) under 20 names | broad store backfilled (`--in-band`); falls back to `playbook\day-trade-universe.txt` when missing/stale/too small (`screen_loop.resolve_universe`) |
| 🟢 RSI2 exact-config replay on Tiingo depth (20 yr) | `.venv\Scripts\python.exe scripts\backtest_rsi2.py --source tiingo --count 5200 --out-dir docs\reviews` | terminal | `docs\reviews\<date>-rsi2-backtest-tiingo.{md,json}` | store backfilled |
| 🟢 Refresh the Tiingo ticker list (drives `--tickers-file` symbol selection for the broad backfill) | `.venv\Scripts\python.exe scripts\tiingo_tickers.py` | terminal | `data\tiingo\_tickers.csv` | none (no token needed) |
| 🟢 Backfill the BROAD Tiingo daily store (every US stock/ETF listed since 2021-09-01; feeds the point-in-time day-trade universe) | `run-tiingo-broad-refresh.bat` (or `.venv\Scripts\python.exe scripts\tiingo_backfill.py --kind broad --tickers-file data\tiingo\_tickers.csv --listed-between 2021-09-01 2099-12-31 --spacing 0.4`) — **SCHEDULED** Sundays 10:00 AM ET as the "Webull Tiingo Broad Refresh" task | terminal / Task Scheduler | one line per symbol + `data\tiingo\_manifest.json` + `data\tiingo\broad_backfill_runs.jsonl`; exit 1 only if nothing fetched | `TIINGO_API_TOKEN` in .env; ticker list refreshed first; never touches the trading path |
| 🟢 Build the point-in-time day-trade universe cache from the broad store | `.venv\Scripts\python.exe scripts\tiingo_universe_cache.py --start 2021-09-01 --end <today> --profile plan-v1 --profile paper-400` | terminal | `data\tiingo\universe\<profile>\<year>.csv` | broad daily store backfilled |
| 🟢 Backfill IEX 1-minute bars for the universe's symbol-months (feeds the ORB backtest) | `.venv\Scripts\python.exe scripts\tiingo_intraday_backfill.py --start 2021-09-01 --end <today> --profile plan-v1 --extra-file playbook\day-trade-universe.txt --extra-month <current YYYY-MM> --spacing 0.4` | terminal | `data\tiingo\iex\<symbol>\<year>-<month>.csv.gz` + `data\tiingo\iex\_iex_manifest.json`; exit 1 only if nothing fetched | universe cache built; `TIINGO_API_TOKEN` in .env |
| 🟢 Run the ORB day-plan backtest on Tiingo IEX 1-minute history | `.venv\Scripts\python.exe scripts\backtest_orb.py --start 2021-09-01 --end <today> --cells all` | terminal | `docs\reviews\<date>-orb-backtest.{md,json}` + `data\backtests\orb\<date>-<cell>-signals.csv` | daily + intraday stores + universe cache backfilled |
| 🟢 External dead-man (PC-off coverage) | One-time: create a free healthchecks.io check — schedule type **Cron**, `0 19 * * 1-5`, timezone America/New_York, grace 2h (the cron states when the 7 PM watchdog RUNS, not the deadline; grace sets the deadline) — pick an alert channel (ntfy/email), then paste its `https://hc-ping.com/<uuid>` ping URL into `.env` as `WEBULL_WATCHDOG_HEALTHCHECK_URL` | healthchecks.io | Phone alert ~9 PM ET when no watchdog ping arrived (PC off / task dead) | Watchdog task registered |
| ⚙️ Prune `logs/` (old rotated SDK logs + oversized suite logs) | `.venv\Scripts\python.exe scripts\prune_logs.py` (also scheduled: "Webull Prune Logs", weekdays 18:50, since 2026-09-19) | terminal | Deletes logs older than 14 days (live logs untouched) and trims `lab_cycle`/`paper_suite`/`sandbox_drill` logs above 5 MB to their last 1 MB | none |
| 🧪 SANDBOX practice — accounts/balances (zero real money, ever) | `.venv\Scripts\python.exe scripts\sandbox_practice.py status` | terminal | 5 simulated accounts + the $1M cash balance | `.env.uat` (PaperTrade portal key; see CLAUDE.md sandbox gotcha) |
| 🧪 SANDBOX practice — self-cleaning order drill (place→detail→replace→cancel a far-from-market limit) | `.venv\Scripts\python.exe scripts\sandbox_practice.py drill AAPL` | terminal | Per-step OK/FAIL + `clean:` flag; rejects with the real broker 417 outside 9:30–16:00 ET | `.env.uat`; market hours for the full cycle. Creds are env-scoped: this CANNOT touch the real book |
| 🟢 Check the TRADING autopilot (config, caps, kill state, today's log) | `autopilot status` (or `.venv\Scripts\python.exe scripts\autopilot.py status`) | terminal | ENABLED?, caps, window, kill-file + KILL state, today's placed/skipped | none (read-only, no network) |
| 🔴 Run ONE autonomous trading cycle now | `autopilot run` | terminal | LIVE orders within caps + summary | `WEBULL_AUTOPILOT_ENABLED=true`; inside the window; token + entitlement |
| 🛑 HALT the trading autopilot (emergency stop) | `autopilot halt` (creates the kill-file, fail-closed) | terminal | Places nothing until resumed | none |
| ▶️ Resume the trading autopilot | `autopilot resume` (removes the kill-file) | terminal | Placement allowed again (within caps/window) | none |
| 🔴 Start TODAY'S autonomous DAY-TRADE session (exception #3; one/day; ORB within plan v1.0 caps; hard flat 11:00) | Weekdays 09:00–10:15 ET, in a terminal yourself: `.venv\Scripts\python.exe scripts\day_trade_session.py --symbols SOFI,PLTR` (the webull-day-session MCP was removed 2026-09-29) | Claude Code chat / terminal | Runner trades autonomously until flat/11:00; ntfy on fill/exit/error; ledger `data\day_session\` | `WEBULL_DAYTRADE_CODEWORD` set; **DRY (previews only) unless owner armed `WEBULL_DAYTRADE_ENABLED=1`** after ≥3 clean DRY sessions + security review |
| 🟢 Day-session status | "day session status" → `session_status` | Claude Code chat | State, lot, last 20 ledger events | none (read-only) |
| 🛑 FREEZE the day-trade session | "halt the day session" → `halt_session` (creates `data\day_session\KILL`; no codeword needed) | Claude Code chat / any file surface | FULL freeze: no new orders AND no runner exits — a resting protective stop stays at the broker; flatten manually in the app if wanted sooner | none |
| ⏸ **PAUSED 2026-09-14 (owner) — task disabled, watchdog key removed; re-enable = `Enable-ScheduledTask` + re-add `paper_day` to the watchdog keys + un-pause the routine row.** Paper day session (ORB runner in the sandbox, weekdays 09:25) | task `Webull Paper Day Session` 09:25 → screens 100 names from 09:45, trades the first fresh ticket in the sandbox (≤2 attempts), flat 11:00 (register: `scripts\register-paper-day-session-task.ps1`); manual: `.venv\Scripts\python.exe scripts\day_trade_paper.py run\|drill\|status` | Task Scheduler / terminal | ntfy pushes titled `PAPER Day session`; `data\day_session_paper\ledger.jsonl`; run-log `paper_day` row (watchdog) | halt: create `dataday_session_paperKILL` (MCP `halt_session` removed 2026-09-29); resume after a halt: delete `data\day_session_paper\KILL` (and `data\day_session\KILL` for the real runner) |
| 🧪 IBS ETF book (paper, sandbox, weekdays 15:56) | task `Webull IBS Book Paper` 15:56 → sells any held fund whose own IBS is above 0.8 at the close, then fills free slots with today's IBS < 0.2 picks (SPY + 11 sector SPDRs, 4 slots, $3,200 virtual book; sells first, buys on actual cash) (register: `scripts\register-ibs-book-paper-task.ps1`); manual: `.venv\Scripts\python.exe scripts\ibs_book_paper.py run\|status\|drill XLU` | Task Scheduler / terminal | `data\ibs_book_paper\ledger.jsonl` (signal/placed/fill/round_trip/summary rows) + `state.json`; run-log `ibs_book_paper` row (watchdog) | halt: create `data\ibs_book_paper\KILL` (a day-session KILL also freezes it); resume: delete it. Sandbox only — never real money |
| 🟢 Paper pool (shadow) — accounting-only replay of BOTH paper books against the shared S6 sizing rule; drives neither runner, changes no decision, submits nothing | Runs as the `Pool-shadow` step of the 17:30 paper suite (`webull_web/runner_cli.py` `PAPER_SUITE`) — no standalone script; trigger by hand: `.venv\Scripts\python.exe -c "from webull_web import pool_shadow_service as m; print(m.run(force=True))"` | Task Scheduler (inside the suite) / terminal | `data\pool\shadow.jsonl` (one journaled row per session: equity, index, slots used, today's takes/skips, divergences) + `data\pool\shadow_state.json`; run-log `pool_shadow` row (watchdog); evening-note line "Pool (shadow): …" | Pure accounting — nothing to arm. **The phase-2 gate is the note's `gate n reconcile, n unmapped` clause only** — `reconcile` = the pool's lots or its journal chain disagree with reality; `unmapped` = a runner fill the pool neither took nor explicitly skipped. The `vs runners n size, n skip` clause beside it is NOT a fault: the pool sizes at equity ÷ 6 while each runner sizes its own way, so it differs on almost every entry. A gate divergence repeats every session until its cause is found and is a phase-2 blocker by design — there is no clear-by-hand fix; read the day's row in `data\pool\shadow.jsonl` |
| ⚙️ Check the real-book sizing rule against live prices (affordability at equity/6 and equity/4, plus the fixed grid) | `.venv\Scripts\python.exe scripts\sizing_guidance.py [--equity N]` | terminal | Which swing names + ETFs one share fits at equity/6 and equity/4, and the same picture at $1,600 / $3,200 / $5,000 / $10,000 / $25,000 | Tiingo store backfilled; `--equity` defaults to the latest real net liq (`netliq_store`), else $1,600. Read-only — no broker call. Rule: `playbook\sizing-rule.md` |
| 🟢 Day-trade MORNING ASSIST (manual trial; read-only) | 09:44: `.venv\Scripts\python.exe scripts\day_trade_launch.py launch screen` (tail `data\day_trade\screen-<date>.log`; each ticket also pushes to the phone). Place the ticket by hand in the Webull app (stop-limit entry + attached/OCO stop). Then `... launch guard --symbol X` (levels from the ticket; override with `--stop/--target/--structure/--one-r/--time-stop/--end`). `... status` / `... stop guard-X`. Journal it in `playbook/journal.md`. | Terminal (detached) + Claude Code chat tailing the logs | Tickets JSON + logs under `data\day_trade\`; phone gets action events only | none — nothing here places an order; `WEBULL_MANAGER_NOTE_NTFY` for pushes |
| 🟢 See my books in kestrel — no server needed | kestrel's profile names `.venv\Scripts\python.exe -m webull_web.feed_export` as a command source (or run it yourself to check it) | terminal / kestrel `profile.toml` | One document: the six books (trades, positions, strategies + backtests, runs, alerts) printed to stdout as strict JSON; exit 1 + one stderr line on failure; places nothing | none — reads local files only, no server required |

---

## The Lab autopilot (strategy R&D — token-free, places nothing)

The Strategy Learning Machine runs itself on a weekday ~5:15pm ET Windows scheduled task. You can check/trigger/pause it from three surfaces. **(This is the R&D autopilot — distinct from the Trading autopilot below, which places real orders.)**

| Goal | Do | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Check the lab's status | `lab-status.bat` (terminal) · "How's the lab doing?" (Claude Desktop, webull-lab) | terminal / Claude Desktop | M, cycle history + staleness, library by status, proven shelf | Desktop needs webull-lab loaded |
| ⚙️ Pause / resume the scheduled autopilot | Windows Task Scheduler → "Webull Lab Cycle" task → Disable / Enable | Task Scheduler | Weekday ~5:15pm ET auto-run stops/starts (Last Run 0x0 when healthy) | The "Webull Lab Cycle" task exists |

---

## Research bench (offline candidate evaluation — no order path, places nothing)

`webull_api/bench/` scores a candidate rule against the live RSI2 + IBS books on frozen develop/confirm
windows (spec `docs/superpowers/specs/2026-09-12-research-bench-design.md`) — the reusable successor to
the one-off backtest scripts (ORB, hold-session, intraday-momentum) above. Read-only research; nothing
here touches the order path, the sandbox, or a runner.

| Goal | Do | Where | Output | Prereqs |
|---|---|---|---|---|
| ⚙️ Score a candidate on a window | `.venv\Scripts\python.exe scripts\bench.py run <name>\|--from-hash <hash> --window develop\|confirm [--override-reason TEXT] [--keep-card] [--note TEXT]` | terminal | Markdown scoreboard card to stdout (standalone stats, base-vs-stacked Calmar, lights, verdict); `--keep-card` also writes it under `data\bench\cards\` (develop) or `docs\reviews\bench\` (confirm, committed) | Reference lists refreshed (`refresh-refs`, below); a candidate module under `webull_api\bench\candidates\` — or `--from-hash` for a feeder-generated rule, rebuilt from its ledger snapshot (the form the digest prints for a pass) |
| 🟢 Check progress / one family / one run's card | `.venv\Scripts\python.exe scripts\bench.py status`, `list [--family F] [--window W]`, `card <run_id>` | terminal | JSON status (runs, passes, confirms); a table of run rows; a re-rendered card | none (reads the ledger) |
| ⚙️ Promote a passed hash to the next stage | `.venv\Scripts\python.exe scripts\bench.py stage <hash> confirm_passed\|paper\|live [--sessions N --closed-trades N] [--note TEXT]` | terminal | Ledger row for the stage transition (refused if the prerequisite stage/evidence is missing) | A `run` already logged that hash |
| 🟢 Calibrate the bench against what the project already believes | `.venv\Scripts\python.exe scripts\bench.py calibrate` | terminal | JSON report of the three §8 identities (rsi2-ref slice identity, base S6 vs the sizing study, IBS lifting RSI2 alone) | Reference lists refreshed |
| ⚙️ Regenerate the reference lists | `.venv\Scripts\python.exe scripts\bench.py refresh-refs` | terminal | `data\bench\refs\rsi2.json` (verbatim copy of the published RSI2 backtest) + `data\bench\refs\ibs.json` (regenerated B2 replay to the frozen confirm end) | Tiingo daily store backfilled |
| ⚙️ Run one feeder night by hand | `.venv\Scripts\python.exe scripts\bench_feeder.py [--batch N] [--budget-min M] [--date YYYY-MM-DD]` | terminal | Writes ledger rows + a digest for the night | Reference lists refreshed |
| 🟢 Read the digest | Open `data\bench\feeder\<date>.md` | file | The night's seed/draw/score summary, passes and near-misses | A feeder night has run |
| ⚙️ Pause / resume the nightly feeder | Create / delete `data\bench\feeder\KILL` | filesystem | Next scheduled run skips (exit 3) / runs normally | none |
| ⚙️ Register the nightly task | `scripts\register-bench-feeder-task.ps1` from an interactive PowerShell | Task Scheduler | "Webull Bench Feeder" task, weekdays 17:35 ET local, no wake-to-run, `StartWhenAvailable` (moved from 19:10 on 2026-09-18: that slot landed after the PC's idle sleep, and a woken PC re-entered Modern Standby before the script could hold its stay-awake request — three frozen nights killed at the 2h limit with no digest). Keep the PC awake through the 17:30 block; the feeder then holds it for its own ~65-min run | Interactive PowerShell — `schtasks /change` hangs prompting for a password on these interactive-logon tasks, per the repo note |

Exit codes: `run` — **0** ran (verdict on the card), **2** refused (confirm-once, missing reference data,
unknown instrument/cost class), **1** error · `stage` — **0** recorded, **2** refused · `calibrate` — **0**
all three identities hold, **1** otherwise · `refresh-refs` — **0** wrote both lists, **1** failed · the
nightly feeder (`bench_feeder.py`) — **0** ran, **3** skipped by the kill file, **1** error before scoring,
**2** partial (an error stopped the loop after some rules scored).

The feeder has **no instance lock** — do not run it by hand during the 17:35 window (through ~19:05 on a full-budget night): two nights would
draw against the same ledger and overwrite the same digest. Task Scheduler's own IgnoreNew covers
task-vs-task, not a hand-started run racing the scheduled one.

`scripts\bench.py run rsi2-ref --window develop` (and `ibs-ref`) now prints REFUSED with overlap 1.00 —
those reference books are inside the base, so a full-overlap refusal is the expected read for a reference
book, not a bug.

---

## The Trading autopilot (autonomous real-money placement) 🔴

The `webull_api/autopilot/` gate places **REAL equity orders unattended** — no codeword, no per-order human step — behind hard caps + a kill-switch. It runs the same engine as `morning_routine` but **places** on the gate's allow instead of drafting. **OFF unless `WEBULL_AUTOPILOT_ENABLED=true`.** (Distinct from the Lab autopilot above, which never trades.)

| Goal | Do | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Check status (config, caps, kill state, today's log) | `autopilot status` (or `python scripts\autopilot.py status`) | terminal | ENABLED?, caps, window, kill-file + KILL state, today's placed/skipped | none (read-only, no network) |
| 🔴 Run ONE autonomous cycle now (reconcile→protect→exits→entries) | `autopilot run` · or "Run the autopilot." (Claude Desktop → `autopilot_run`) | terminal / Claude Desktop | JSON {placed, skipped, errors} + LIVE orders within caps | `WEBULL_AUTOPILOT_ENABLED=true`; inside the window; token + entitlement |
| 🛑 HALT everything (emergency stop) | `autopilot halt` — creates the kill-file (fail-closed) | terminal (or create the `KILL` file from a synced folder, if the kill-file is on OneDrive) | Autopilot places nothing until resumed | none |
| ▶️ Resume after a halt | `autopilot resume` — removes the kill-file | terminal | Placement allowed again (within caps/window) | none |
| ⚙️ The scheduled evening run — **LIVE since 2026-07-17** as the "Webull Autopilot" task (weekdays **5:45 PM**, wake-from-sleep) | Pause/resume: `Disable-ScheduledTask` / `Enable-ScheduledTask -TaskName "Webull Autopilot"` · re-register: `scripts\register-autopilot-task.ps1` | Task Scheduler | One gated cycle per weekday evening; decisions in `autopilot\log\<date>.jsonl`, summarized in the nightly manager note | PC on/wakeable at 5:45 PM; token valid |

**Go-live (completed 2026-07-17):** security verification re-run (gate/runner audit + 142 tests + an out-of-window smoke run, audit log verified), then the owner executed both human-gated steps — the smoke `run` and the task registration (the harness blocks the assistant from both, by design). Full procedure + the security-review gate: `docs/superpowers/specs/2026-07-07-autonomous-placement-gate-design.md` §13. **Emergency stop any time:** `autopilot halt` (also allowed from a phone session; resume is desktop-only). Since 2026-08-15 closing SELLs clear a resting protective stop first (cancel-then-sell, verified against the broker before placing) — the 417 REVERSE_OPTION rejections that blocked exits on protected positions are gone.

---

## Safety — what can and can't touch real money

| Goal | The rule | Where | Output | Prereqs |
|---|---|---|---|---|
| 🟢 Default posture: reading & analyzing never places a real order | All MCP connectors except webull-trade (`place_order`, codeword) are read/simulate-only, and so is every terminal script except `scripts/place_order.py` (dry-run by default; a real order needs `--confirm` + a typed CONFIRM) and `scripts/autopilot.py run` (exception #2, the scheduled autopilot); dry-run (`confirm=False`) is the default everywhere | everywhere | Previews / drafts / simulations only | none |
| 🔴 Non-web real-order surface #1 (authorized exception) | Claude Desktop `webull-trade` `place_order` — gated by secret `WEBULL_TRADE_CODEWORD` (you type it) + per-order cap `WEBULL_TRADE_MAX_NOTIONAL` ($500 default), opt-in | Claude Desktop (webull-trade) | A real order only if codeword + cap pass; else rejected | `WEBULL_TRADE_CODEWORD` in .env. `draft_order` stays submit-free. The other kept MCP, `webull_mcp`, is place-free by source guard (paper/strategy/lab/day-session MCPs removed 2026-09-29) |
| 🔴 Non-web real-order surface #2 (authorized exception, autonomous) | The **Trading autopilot** (`autopilot_run` / `autopilot run`) — NO codeword; gated by `WEBULL_AUTOPILOT_ENABLED` + kill-file + per-order/positions/day/loss caps + window. Fail-closed; `should_submit` untouched | terminal / Claude Desktop (webull-trade) | Real orders only if enabled + gate allows; audit-logged | `WEBULL_AUTOPILOT_ENABLED=true` (opt-in). Emergency stop: `autopilot halt`. `draft_order`/`morning_routine` stay submit-free |
| 🔴 Non-web real-order surface #3 (authorized exception, decisions) | The **decision executor** (autopilot DECISIONS stage, 2026-08-07): owner/standing decisions queued via `scripts/queue_decision.py` execute at the next armed run — equity SELL/BUY through `authorize`, long options through `authorize_option` — SINGLE **or debit VERTICAL**, always order-side BUY (a credit lands on SELL and is denied), under `WEBULL_OPTIONS_MAX_DEBIT`, and verticals also under the width + `WEBULL_OPTIONS_MAX_DEBIT_FRAC_OF_WIDTH` (65%) edge filter. BUYs age 120 min first (veto window). Trigger kinds: `immediate` / `green_day` / `price_above` / `price_below` / **`rsi2_above`** (2026-08-15, `--threshold`, default 70 — the mean-reversion exit band, evaluated on a fresh same-day bar, fail-closed) | terminal (queue) → scheduled runs (place) | Real orders only when armed + triggered + gate allows; every skip audit-logged | `WEBULL_AUTOPILOT_DECISIONS_ENABLED=1` (opt-in, owner-set). Arming: `docs/superpowers/specs/2026-08-07-arming-checklist.md`. Same kill file halts |

| 🟡 NOT a real-order surface: the real-book RSI2 runner (2026-08-15) | `rsi2_real_service` (12th suite step) is **QUEUE-ONLY**: sizes whole-share entries from **settled cash** and writes executable-decision rows; the DECISIONS stage above is what places. Source-guard-tested place-free; cancels only exit rows its own ledger recorded | paper-suite task (17:30) | queued rows + run-log `rsi2_real` (no_op while disarmed) | **OFF** until the owner completes the arming checklist: proof bar 30/30 · ~$1.5k funding · the 09:31 autopilot trigger · security review · `WEBULL_RSI2_REAL_ENABLED=true` — spec `docs/superpowers/specs/2026-08-15-real-book-rsi2-path-design.md` |

---

## Regenerate this guide

```
Build me an operator's guide at docs/operator-guide.md that maps WHAT I WANT TO DO -> THE EXACT WAY TO TRIGGER IT. Discover the capability surface from the repo (do not guess): CLAUDE.md; every *_mcp/ package's README + manifest + the @mcp.tool docstrings in its server.py; the repo-root *.bat and python -m entrypoints; web/src/lib/nav.ts + web/src/App.tsx. Organize by SURFACE (Claude Desktop / Terminal / Web app / Autopilot / Safety) as tables with columns Goal | Trigger | Where | Output | Prereqs, plus a read-only/paper/real-order marker. For each MCP tool give a natural-language example phrase that reliably triggers it. Keep it concise, scannable, operator-focused (not implementation detail). Put a "How to regenerate" note at the top and paste this exact prompt at the bottom. Then show me the file.
```
