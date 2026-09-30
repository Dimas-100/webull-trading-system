> **Public repo note.** The `archive/...` tags below live in the private history, not in this public repo,
> so the `git checkout archive/...` commands work only against that private history.

# Archived 2026-09-29 — webull is RSI2-only

Owner decision 2026-09-29: one real strategy (RSI2 at S6 sizing). Code that only the parked systems used was
deleted from the tree; it is preserved exactly as it last worked at git tag **`archive/pre-rsi2-only-2026-09-29`**:

    git checkout archive/pre-rsi2-only-2026-09-29 -- <path> [<path> ...]

Restore a piece together with its tests (same command; the tests were deleted alongside) and, for a suite step,
put its row back in `webull_web/runner_cli.PAPER_SUITE`, its label in `manager_note_service.RUNNER_LABELS` and
its key in `watchdog_service.EXPECTED_KEYS`; for a scheduled system, its `tracks.py`/`routine.py` rows too (the
tag has them). `data/` was not touched.

- **Day session (exception #3) + day-trade assist:** `webull_api/day_session/`, `webull_api/day_trade_assist/`,
  `scripts/day_trade_{guard,launch,paper,screen,session,universe}.py`, `scripts/register-paper-day-session-task.ps1`,
  `playbook/day-trade-checklist.md`, `playbook/day-trade-universe.txt`. NYSE holidays moved to
  `webull_api/market_calendar.py`.
- **Session grid:** `webull_api/session_grid/`, `webull_web/session_grid_ledger.py`, `scripts/session_grid_*.py`,
  `scripts/register-session-grid-paper-task.ps1`, `playbook/session-grid-runbook.md`.
- **Research bench + Lab tooling:** `webull_api/bench/` (candidates, feeder, `seed_history.jsonl`),
  `scripts/bench.py`, `scripts/bench_feeder.py`, `scripts/bench_refs.py`, `scripts/lab_status.py`,
  `run-bench-feeder.bat`, `lab-status.bat`, `scripts/register-bench-feeder-task.ps1`,
  `webull_web/strategy_store.py`, `webull_api/strategy/signal.py`.
  (`webull_api/lab/` + `webull_web/lab_service.py`/`lab_store.py` STAY — see "kept" below.)
- **IBS ETF book + sandbox:** `webull_api/ibs_book/`, `webull_api/sandbox/`, `scripts/ibs_book_paper.py`,
  `scripts/sandbox_practice.py`, `scripts/register-ibs-book-paper-task.ps1`, `scripts/backtest_ibs_book.py`,
  `scripts/export_ibs_backtest_trades.py`.
- **Pool shadow:** `webull_api/pool/{ledger,shadow,sources}.py`, `webull_web/pool_shadow_service.py`
  (`webull_api/pool/affordability.py` stays for `scripts/sizing_guidance.py`).
- **Closed research families:** `webull_api/{intraday_momentum,intraday_trend,etf_intraday_mr,etf_session,
  hold_session,overnight,rsi2_short}/`, `scripts/backtest_{etf_candidates,etf_intraday_mr,hold_candidates,
  intraday_momentum,orb,orb_candidates,overnight,rsi2_short}.py`, `scripts/orb_r2_diagnostics.py`.
- **Parked suite steps:** `webull_web/{paper_exits,paper_options_exits,options_entry,proven_runner,scorecard,
  judgment}_service.py`, `webull_web/claude_cli.py`, `webull_api/iv_log.py`, `scripts/{paper_eod,
  paper_eod_options,options_entry,proven_paper,scorecard,scan_ledger}.py`, `playbook/options-trading-plan.md`,
  `playbook/skills/options-planner/`. (`runner_cli.PARKED_SUITE` keeps only the paper RSI2 row.)
- **Tiingo intraday + broad universe:** `webull_api/tiingo/intraday.py`, `scripts/tiingo_intraday_backfill.py`,
  `scripts/tiingo_tickers.py`, `scripts/tiingo_universe_cache.py`, `scripts/build_rank_universe.py`,
  `run-tiingo-broad-refresh.bat`, `scripts/register-tiingo-broad-refresh-task.ps1`.
- **Tests:** the matching `tests/` files and folders (`tests/{day_session,day_trade_assist,session_grid,ibs_book,
  pool,intraday_momentum,etf_intraday_mr,overnight,rsi2_short,tiingo}/`, `tests/test_bench_*`, etc.).
- **Registry rows removed:** tracks `day_orb` (paper+real), `lab`, `options_paper`, `swing_ibs_etf`; the routine's
  day-session, session-grid, bench, Lab-cycle and scorecard rows; the feed's `ibs-paper` / `session-grid-paper`
  books and `ibs` / `session-grid` strategies.

**Kept although parked-looking (live code imports them):** `webull_web/paper_service.py`, `paper_options_service.py`,
`webull_api/paper/` (netliq snapshot + rsi2_real + flows); `webull_web/rsi2_service.py`/`rsi2_store.py`
(rsi2_real_service + decisions_service); `webull_web/lab_service.py`/`lab_store.py` + `webull_api/lab/`
(manager note, exit plans, north star, decisions, exec_quality, tiingo_backfill); `scan_ledger_service.py`,
`scanner_store.py`, `judgment_store.py`, `scorecard_store.py`, `webull_api/scorecard.py` (manager note, north star);
`webull_api/options*.py` (autopilot, safety, webull_mcp); `webull_api/swing/`, `discovery.py` (autopilot entry
screen, webull_trade_mcp); `webull_api/journal/` + `strategy/replay.py` (journal normalize).

**Operator notes:** do not re-enable the disabled tasks Session Grid Paper, Bench Feeder, IBS Book Paper, Paper
Day Session or Tiingo Broad Refresh — their scripts are gone. Exception #3 (the day session) no longer exists.

# Archived 2026-09-28 — webull is systems-only

The owner retired the dashboard ("it should not have to worry about maintaining a dashboard") and the two
connectors that don't serve the trading systems. kestrel is the one dashboard now: it runs
`python -m webull_web.feed_export` itself. Spec: `docs/superpowers/specs/2026-09-28-systems-only-design.md`.

Everything below is preserved exactly as it last worked at the git tag
**`archive/pre-systems-only-2026-09-28`**. Restore any piece with:

    git checkout archive/pre-systems-only-2026-09-28 -- <path> [<path> ...]

A restored piece needs its tests too (same command, the test paths below), and the web app also needs
`fastapi`, `uvicorn[standard]` and `anthropic` back in `requirements.txt`.

## The dashboard (web app, React front end, Copilot)
- `web/` — the whole React front end (273 files).
- `start-server.bat`, `stop-server.bat`.
- `webull_web/__main__.py`, `webull_web/app.py`, `webull_web/errors.py`, `webull_web/scheduler.py`.
- `webull_web/routers/` — the whole package (`__init__.py`, `activity.py`, `autopilot.py`, `bench.py`,
  `cockpit.py`, `copilot.py`, `feed.py`, `instrument.py`, `journal.py`, `lab.py`, `misc.py`, `overview.py`,
  `overview_real.py`, `paper.py`, `paper_options.py`, `plan.py`, `strategy.py`, `swing.py`, `viz.py`).
- `webull_web/copilot/` — the whole package (`__init__.py`, `agent.py`, `tools.py`).
- `webull_web/activity_service.py`, `webull_web/arming_service.py`, `webull_web/bench_service.py`,
  `webull_web/bench_view.py`, `webull_web/cockpit_strip.py`, `webull_web/cockpit_strip_service.py`,
  `webull_web/holdings_service.py`, `webull_web/journal_md.py`, `webull_web/overview_claude.py`,
  `webull_web/overview_report.py`, `webull_web/overview_report_service.py`, `webull_web/overview_reports_store.py`,
  `webull_web/performance_service.py`, `webull_web/plan_rsi2.py`, `webull_web/plan_service.py`,
  `webull_web/real_account.py`, `webull_web/real_header_service.py`, `webull_web/spark_cache.py`,
  `webull_web/strategy_parse.py`, `webull_web/theses_store.py`, `webull_web/tonight_service.py`,
  `webull_web/trade_anatomy_service.py`, `webull_web/viz_service.py`.
- `webull_api/activity.py`, `webull_api/journal/memory.py` (used only by the Activity page and the Copilot).

## webull-analysis connector (+ the modules only it used)
- `webull_analysis_mcp/` — the whole package, incl. `claude-extension/`.
- `webull_web/catalysts_data.py`, `webull_web/equity_metrics.py`, `webull_web/fund_holdings.py`,
  `webull_web/portfolio_bridge.py`, `webull_web/portfolio_risk_service.py`, `webull_web/trade_check_service.py`.
- `webull_api/catalysts_metrics.py`, `webull_api/fundamentals_metrics.py`, `webull_api/instrument.py`,
  `webull_api/portfolio_risk.py`, `webull_api/trade_check.py`.

## snaptrade-portfolio connector
- `snaptrade_mcp/` — the whole package, incl. `claude-extension/`.
- `snaptrade_api/` — the whole package.
- `requirements-snaptrade.txt`, `requirements-snaptrade.lock`.

## Tests removed with them
- `tests/test_activity.py`, `tests/test_activity_api.py`, `tests/test_activity_autopilot_day.py`,
  `tests/test_activity_service.py`, `tests/test_arming_service.py`, `tests/test_autopilot_halt_api.py`,
  `tests/test_bench_route.py`, `tests/test_bench_view.py`, `tests/test_cadence_scheduler.py`,
  `tests/test_catalysts_data.py`, `tests/test_catalysts_metrics.py`, `tests/test_cockpit_route.py`,
  `tests/test_cockpit_strip.py`, `tests/test_copilot_agent.py`, `tests/test_copilot_identity.py`,
  `tests/test_copilot_memory.py`, `tests/test_copilot_model.py`, `tests/test_copilot_tools.py`,
  `tests/test_dev_mode.py`, `tests/test_equity_metrics.py`, `tests/test_equity_metrics_api.py`,
  `tests/test_feed_routes.py`, `tests/test_fund_holdings.py`, `tests/test_fundamentals_metrics.py`,
  `tests/test_holdings_service.py`, `tests/test_instrument.py`, `tests/test_journal_api.py`,
  `tests/test_journal_md.py`, `tests/test_journal_memory.py`, `tests/test_lab_routes.py`,
  `tests/test_levels_api.py`, `tests/test_overview_api.py`, `tests/test_overview_claude.py`,
  `tests/test_overview_real_routes.py`, `tests/test_overview_report.py`,
  `tests/test_overview_report_service.py`, `tests/test_overview_reports_store.py`, `tests/test_paper_api.py`,
  `tests/test_plan_rsi2.py`, `tests/test_plan_service.py`, `tests/test_portfolio_bridge.py`,
  `tests/test_portfolio_risk.py`, `tests/test_portfolio_risk_service.py`, `tests/test_real_account.py`,
  `tests/test_real_header_service.py`, `tests/test_scheduler.py`, `tests/test_snaptrade_mcp.py`,
  `tests/test_snaptrade_normalize.py`, `tests/test_snaptrade_portfolio.py`, `tests/test_spark_cache.py`,
  `tests/test_strategy_api.py`, `tests/test_strategy_parse.py`, `tests/test_swing_api.py`,
  `tests/test_theses_store.py`, `tests/test_tonight_service.py`, `tests/test_trade_anatomy_service.py`,
  `tests/test_trade_check.py`, `tests/test_trade_check_api.py`, `tests/test_trade_check_service.py`,
  `tests/test_viz_routes.py`, `tests/test_viz_service.py`, `tests/test_web_options.py`,
  `tests/test_web_options_analytics.py`, `tests/test_web_options_orders.py`, `tests/test_web_paper_options.py`,
  `tests/test_webull_analysis_mcp.py`, `tests/test_webull_web_main.py`.

## What changed for the operator
- Real orders go through the three sanctioned paths only: the codeword `place_order` (webull-trade), the
  autopilot, the day session. The browser arm + typed-CONFIRM ticket went with the web app.
- The manager's note and the entry judgment read the SPY regime from `webull_web/regime.py`.
- Claude Desktop: remove the **webull-analysis** and **snaptrade-portfolio** extensions (they no longer start).
- Nothing uses `ANTHROPIC_API_KEY` any more (the Copilot was its last user).
