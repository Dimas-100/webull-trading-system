# webull-trading-system

A personal Python trading system on the [Webull OpenAPI](https://developer.webull.com/apis/docs/).
It runs **one real strategy — RSI2 swing on US equities & ETFs** — with its backtests, scheduled
runners and safety rails, operating on **production** (a developer-portal key is prod-only; see the
Environment note), with dry-run / typed-`CONFIRM` safety rails on every order.

**This repo is the trading system only — there is no web app or dashboard here.** The dashboard was
retired 2026-09-28 and the parked strategies 2026-09-29 (both preserved at git tags, see
`docs/ARCHIVE.md`). [kestrel](https://github.com/Dimas-100/kestrel) is the dashboard: it reads this
repo by running `python -m webull_web.feed_export` itself.

> **Read this first.** This is a personal project that places **real-money orders**. It is shared as a
> reference, not as financial advice and not as a supported product. Nothing here is a recommendation to
> trade. If you run it, you are responsible for every order it sends.

**Want to build something like this yourself?** Start with
[ai-trading-kit](https://github.com/Dimas-100/ai-trading-kit): it connects your own brokerage to your AI
assistant (read-only), then walks you through testing and practicing ideas with pretend money.

> **Public repo note.** The build log, the per-feature design specs and plans, the proof-phase charter,
> the backtest review archive and the trade journals are kept private. Paths in code comments and docs
> that point at `docs/BUILD-LOG.md`, `docs/superpowers/`, `docs/proof-phase-charter.md` or
> `playbook/journal.md` refer to those private files. The `archive/...` git tags named in
> `docs/ARCHIVE.md` live in the private history too.

> Built on `webull-openapi-python-sdk==2.0.10` (import namespace `webull.*`). Real-money order
> placement is US equities & ETFs; the MCP connectors add read-only market data, portfolio and
> options-chain analytics. Real-money trading requires deliberate opt-in behind the two
> AUTHORIZED exceptions (CLAUDE.md).

> **Environment note (important):** A Webull developer-portal App Key is **production-only** —
> the UAT sandbox returns `404` for it — so set `WEBULL_ENV=prod`. There is no usable test
> environment for a real key, so write-safety rests entirely on the dry-run/confirm rails
> (which it does, by design). Market data additionally requires the **free OpenAPI quotes
> entitlement** (Nasdaq Basic – Non Display; `401 "Insufficient permission…"` without it);
> account/portfolio reads and trading are unaffected.

## Map of the project

![System map: evening decide, morning act, read-only watch, research](docs/images/system-map.svg)

| I want to… | Go to |
|---|---|
| Understand the live strategy and its books | `docs/program-map.md` |
| See what runs when | [The trading day](#the-trading-day) · `webull_web/routine.py` |
| Find the exact command for a task | `docs/operator-guide.md` |
| Read how an order is gated | [Trading safety](#trading-safety-how-an-order-is-gated) · `webull_api/safety.py` |
| Change how entries and exits are decided | `webull_api/strategy/rsi2.py` · `webull_web/rsi2_real_service.py` |
| Change what the autopilot may do | `webull_api/autopilot/gate.py` (needs a security review) |
| Re-run the backtests | `scripts/backtest_rsi2.py` · `scripts/sizing_study.py` |
| See the dashboard | [kestrel](https://github.com/Dimas-100/kestrel) — not in this repo |
| Restore something that was archived | `docs/ARCHIVE.md` |
| See what is planned next | `docs/ROADMAP.md` |

## What it does

- **Market data** (read-only): quotes, snapshots, historical OHLCV bars (HTTP) + live
  streaming quotes (MQTT).
- **Portfolio** (read-only): accounts, balances, positions.
- **Trading**: preview / place / modify / cancel equity orders behind a dry-run guard,
  plus a live gRPC order/position event stream.
- **The RSI2 system**: the evening suite queues decisions (`webull_web/rsi2_real_service.py`,
  queue-only), the owner-gated **autopilot** places, protects (resting stops) and exits them, a
  watchdog checks the runs, and a nightly note names the trades.
- **Backtests**: the RSI2 replay, stop/exit-band studies and the S6 sizing study (`scripts/backtest_*.py`,
  `scripts/sizing_study.py`).

## The trading day

![Daily timeline of scheduled runs, Eastern Time](docs/images/daily-timeline.svg)

The table this is drawn from lives in `docs/program-map.md` (rendered from `webull_web/routine.py`).

## Setup

```powershell
# 1. Create the virtualenv (Python 3.11.9 — see .python-version) and install deps
py -3.11 -m venv .venv          # or: & "C:\Program Files\Python311\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c requirements.lock   # -c pins the exact tested set

# 2. Create your .env from the template and fill in your keys
copy .env.example .env          # then edit .env
```

**Optional integration** (degrades gracefully if absent): `FINNHUB_API_KEY` — earnings-date checks
used by the RSI2 and setups services (free tier).

**Claude Desktop MCP connectors** live under `webull_mcp/` + `webull_trade_mcp/` — read-only/simulated
except the codeword-gated `webull-trade` `place_order`. The other real-order path is the
owner-gated **autopilot** (`webull_api/autopilot/` — OFF by default, kill-switch + hard caps); see
CLAUDE.md "AUTHORIZED exceptions".

`.env` (gitignored — never commit it):

```
WEBULL_APP_KEY=...        # from the Webull developer site (after API access is approved)
WEBULL_APP_SECRET=...
WEBULL_REGION=us
WEBULL_ENV=prod           # a developer-portal key is PROD-ONLY (UAT 404s it) — see Environment note
WEBULL_OPENAPI_TOKEN_DIR=.webull-tokens   # where the SDK stores the 2FA token (gitignored)
```

### Why secrets live in `.env`, never in git

A secret committed even once stays in git history forever (even after you "delete" it),
and private repos get cloned, forked, backed up, and shared. The only fix for a leaked key
is **rotation**. So keys live in `.env` (gitignored); `.env.example` documents the layout
with placeholders.

### First run & 2FA

The SDK manages an access token (stored under `WEBULL_OPENAPI_TOKEN_DIR`). The **first**
call creates it in a "pending verification" state and you approve it with an **SMS code in
the Webull app**. After that it lasts ~15 days and refreshes automatically.

## Run order

Run each from the repo root with the venv Python. **Start with `verify_connection` — don't
move on until it lists your accounts.**

```powershell
.\.venv\Scripts\python.exe scripts\verify_connection.py                 # lists accounts (the gate)
.\.venv\Scripts\python.exe scripts\get_quote.py AAPL                    # quote + recent bars
.\.venv\Scripts\python.exe scripts\portfolio_summary.py                 # balance + positions
.\.venv\Scripts\python.exe scripts\place_order.py --account <ID> --symbol AAPL --side BUY --qty 1 --limit 1
.\.venv\Scripts\python.exe scripts\stream_quotes.py AAPL                # live MQTT quotes (Ctrl+C)
.\.venv\Scripts\python.exe scripts\watch_orders.py <ID>                 # live order events (Ctrl+C)
```

`place_order.py` without `--confirm` is a **dry run**: it prints a preview and Webull's
server-side validation but submits nothing.

## Trading safety (how an order is gated)

![Order safety gates: three entry paths, validate, submit gate, dry run by default](docs/images/order-safety-gates.svg)

1. `safety.validate_order` — sanity checks (side, qty > 0, order type, limit price within a
   deviation guard of the last price, etc.).
2. **Dry run by default** — `trading.place(..., confirm=False)` previews via Webull's
   non-executing `preview_order` and stops.
3. **Submit** only with `confirm=True`; the CLI also requires you to type `CONFIRM`. The only
   other real-order paths are the two AUTHORIZED EXCEPTIONS (CLAUDE.md): the codeword-gated
   `place_order` MCP tool and the autopilot.
4. The submit gate is **`confirm=True`** (`safety.should_submit` returns `bool(confirm)`); `WEBULL_ENV`
   only selects the endpoint host + prints the loud prod banner — it is not a second gate. Because a
   developer key runs on prod, every `confirm=True` submit is real money, so the dry-run default + the
   typed `CONFIRM` are what make an accidental live order impossible.

## Placing a live order (CLI)

There is no test→prod promotion — a developer key is prod from day one (`WEBULL_ENV=prod`, red
`REAL MONEY` banner on every load). To place a live order: `place_order.py ... --confirm`, then type
`CONFIRM` at the prompt — you will see `*** SUBMITTING A LIVE REAL-MONEY ORDER TO PRODUCTION ***`.
Start tiny and verify in the Webull app.

## Project layout (and why)

```
webull_api/
  config.py        # loads .env -> Settings; maps (env, service) -> endpoint host; prod banner
  client.py        # builds & caches one ApiClient per service (trade/data) + the facades
  safety.py        # PURE order build/validate + the submit gate — no network, unit-tested
  trading.py       # the ONLY real-money seam: preview/place/cancel/modify, routed through safety
  market_data.py   # HTTP quotes / snapshots / bars
  portfolio.py     # accounts / balance / positions (read-only)
  streaming/
    market_stream.py   # live MQTT quotes (DataStreamingClient)
    order_events.py    # live gRPC order/position events (TradeEventsClient)
  autopilot/       # owner-gated unattended placement (fail-closed gate; OFF by default)
  tiingo/ sizing_study/   # long-history bars for the backtests + the S6 sizing study
  + swing/ lab/ journal/ paper/ strategy/ …   # support packages the live RSI2 path imports
webull_web/        # RSI2 services + stores, runner_cli (the suite), watchdog, kestrel's feed exporter -- no web code (the name is historical)
webull_mcp/ webull_trade_mcp/     # Claude Desktop / Claude Code MCP connectors
scripts/           # runnable entry points: demos, suite/autopilot/watchdog, backtests
playbook/          # trading plans + Claude playbook skills
tests/             # pytest suite (~2,080 tests) across api, runners, gates, MCPs
docs/              # ROADMAP · operator-guide · program-map · ARCHIVE · images/ · reviews/ (backtest results the code reads)
```

The design separates **read** surfaces from the single **write** surface (`trading.py`),
and isolates all order-safety decisions into a pure module so they can be tested without
hitting the API. Switching environments only swaps endpoint strings.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Unit tests are pure/mocked (no network, no real orders). Live behavior is verified by running the
scripts against **prod** (there is no usable UAT for a developer key — see the Environment note);
start tiny and keep orders in dry-run until you mean it.

## Scope

In: US equity/ETF trading (dry-run by default), the RSI2 real book (suite + autopilot + watchdog),
RSI2 backtesting and sizing, HTTP + streaming market data, portfolio reads, read-only MCP connectors,
and the feed kestrel reads.
Out: any web app or dashboard (that is kestrel), the parked strategies (day session, session grid,
IBS book, research bench, options sleeve — archived), crypto trading, short sells, multi-leg/algo
equity orders, typed response models.

Where to read more: `docs/ROADMAP.md` (next up) · `docs/program-map.md` (the tracks, their books and the
day) · `docs/operator-guide.md` (what-I-want-to-do → the exact trigger) · `docs/ARCHIVE.md` (what was
archived 2026-09-28 / 2026-09-29).
