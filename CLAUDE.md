# CLAUDE.md — project memory for the Webull OpenAPI toolkit

> This file is read automatically at the start of **every** session, so every word is a
> context tax paid each time. Keep it small: only what is *always* relevant.

> **Maintaining this file (read before editing it).** When you ship a feature, do **NOT** add a
> status paragraph here — the detail belongs in its `docs/superpowers/{specs,plans}/` doc + git
> history; add at most a one-line entry to `docs/BUILD-LOG.md`. Only edit CLAUDE.md when a
> **convention, gotcha, or invariant** actually changes. Roadmap/next-up lives in `docs/ROADMAP.md`
> (+ memory). Keep this file roughly to its current length; if it grows, prune it back.

## What this is

A personal Python toolkit wrapping **Webull's OpenAPI** for:

1. **Market data** (read-only) — quotes, snapshots, historical OHLCV bars (HTTP) and live
   streaming quotes (MQTT).
2. **Portfolio** (read-only) — accounts, balances, positions.
3. **Trading** — place / preview / modify / cancel **US equity & ETF** orders, behind
   layered dry-run safety rails, plus a live gRPC order/position event stream.

This repo runs ONE real strategy since 2026-09-29 — **RSI2 on the real book** (the 17:30 suite queues, the
**autopilot** places/protects/exits) plus RSI2 backtesting and **Claude Desktop MCP connectors**; the parked
systems are archived at git tag `archive/pre-rsi2-only-2026-09-29` (`docs/ARCHIVE.md`) — and kestrel (a separate repo) shows them by
running `python -m webull_web.feed_export` itself; `webull_web` holds no web code (the name is
historical — `docs/ARCHIVE.md`). Each system is described in `docs/BUILD-LOG.md`.

**Goal:** a safe, reusable personal tool that makes a real-money order impossible to send by
accident. Also a teaching scaffold — the layout is meant to be reproducible for future projects.
**North star (owner pivot 2026-07-07):** the account is Claude-managed — Claude researches, places
(paper today via the scheduled runner suite; real money behind the owner-gated autopilot) and learns;
the owner funds and watches. Charter: `docs/proof-phase-charter.md`; live program state: the
`claude-managed-portfolio-review` project memory.

## Stack & why

- **Python 3.11.9** — satisfies the SDK's stated range (the GitHub README caps at 3.11;
  PyPI says up to 3.13; 3.11.9 is safe under both).
- **`webull-openapi-python-sdk==2.0.10`** — the official SDK. It signs every request
  (HMAC-SHA1) and manages the access token, so we don't hand-roll auth. **Import
  namespace is `webull.*`** (NOT `webullsdk*` — that's an older/other SDK that is not
  installed here; some online docs show it, ignore them).
- **`python-dotenv`** — loads `.env`. **`pytest`** — unit tests.
- **Claude auth:** nothing scheduled calls Claude (its last caller, the entry judgment + `claude_cli.py`, was
  archived 2026-09-29). The trading path uses no Claude at all.

## Conventions (load-bearing)

- **Env default vs reality.** `WEBULL_ENV` defaults to `test` (UAT) in code, BUT the UAT host
  returns a blanket `404 Route Not Found` for a *production* App Key (see Gotchas). A real
  Webull developer-portal key only works against **prod**, so this account's `.env` is
  `WEBULL_ENV=prod`. With no usable test env, **write-safety rests entirely on the
  dry-run/confirm/prod-gate rails** in `safety.py` + `trading.py` — not on an isolated test
  environment. Prod prints a loud banner on every load.
- **Switching env changes ONLY endpoint strings** (`config.py` `_ENDPOINTS`). No other
  code differs between test and prod.
- **Dry-run trading.** `trading.place(..., confirm=False)` is the default: it validates,
  prints a preview, calls Webull's non-executing `preview_order`, and **does not submit**.
  Submission needs `confirm=True`; the `place_order.py` CLI also demands a typed `CONFIRM`.
  The submit gate is **`confirm=True` alone** (`safety.should_submit` returns `bool(confirm)`); `WEBULL_ENV`
  only selects the endpoint host + prod banner, it is NOT a second gate. Because the key is prod-only, every
  `confirm=True` submit is real money — the dry-run default + typed `CONFIRM` are what prevent an accident.
- **All order safety is in `safety.py`** (pure, no network, fully unit-tested). `trading.py`
  is the only module that can place/cancel/modify a real order.
- **The AI cannot submit a real order from the CLI.** The harness auto-classifier blocks
  `trading.place(confirm=True)` from Bash by design; a real order goes only through the two
  AUTHORIZED EXCEPTIONS (codeword-gated `place_order`, `autopilot_run`).
  `place_order` dry-run / `preview_order` are always safe.
- **Secrets & the 2FA token never enter git.** `.env`, `conf/`, `.webull-tokens/`, and
  `*.log` are gitignored. A committed secret is compromised forever (git history is
  permanent); the only remedy is rotation.
- **Scripts add the repo root to `sys.path`** (one line at the top: `sys.path.insert(0,
  str(Path(__file__).resolve().parent.parent))`) so `import webull_api` works when a
  script is run directly.

## Run, test & apply changes

- **Backend tests:** `.venv/Scripts/python.exe -m pytest`.
- **New MCP tools** need a Claude Desktop / Claude Code restart to load.

## Verified SDK ground truth

**Imports / connect:**
```python
from webull.core.client import ApiClient
from webull.trade.trade_client import TradeClient   # .account_v2, .order_v2
from webull.data.data_client import DataClient      # .market_data, .instrument
api_client = ApiClient(app_key, app_secret, region_id)   # region_id="us"
api_client.add_endpoint(region_id, host)                 # how the env host is set
```
Responses are requests-style: `res.status_code`, `res.json()`.

**Account (`trade_client.account_v2`):** `get_account_list()`, `get_account_balance(account_id)`,
`get_account_position(account_id)`.

**Orders (`trade_client.order_v2`):** `preview_order(account_id, orders)`,
`place_order(account_id, orders)`, `replace_order(account_id, modify_orders)`,
`cancel_order(account_id, client_order_id)`, `get_order_detail(account_id, client_order_id)`.

**Simple equity order dict** (no `combo_type` needed; all values strings):
```python
{"client_order_id": uuid4().hex, "symbol": "AAPL", "instrument_type": "EQUITY",
 "market": "US", "order_type": "LIMIT", "limit_price": "26", "quantity": "1",
 "support_trading_session": "CORE", "side": "BUY", "time_in_force": "DAY",
 "entrust_type": "QTY"}   # modify: {"client_order_id": ..., "quantity": ..., "limit_price": ...}
```

**Market data (`data_client.market_data`)** — enums as `.name`:
`get_quotes(sym, Category.US_STOCK.name)`, `get_snapshot(syms, cat)`,
`get_history_bar(sym, Category.US_STOCK.name, Timespan.D.name, count="200")`.

**Streaming quotes (MQTT):** `DataStreamingClient(app_key, app_secret, region, uuid4().hex,
http_host=<data>, mqtt_host=<data_mqtt>)`; set `on_connect_success`/`on_quotes_message`/
`on_subscribe_success`; subscribe inside on-connect; `connect_and_loop_forever()` blocks.

**Order events (gRPC):** `TradeEventsClient(app_key, app_secret, region, host=<events or None>)`;
set `on_events_message(event_type, subscribe_type, payload, raw)`; `do_subscribe([account_id])`
blocks. **UAT requires an explicit `host=`.**

**Endpoint hosts — HTTP market data SHARES the trade host; `data-api.*` is MQTT-only:** prod
`trade`=`data`=api.webull.com, `data_mqtt`=data-api.webull.com, `events`=events-api.webull.com
(test hosts + the corrected table live in `config.py` `_ENDPOINTS`).

## Gotchas

- **UAT sandbox** (verified 2026-08-16): a PaperTrade-portal key (`.env.uat`) + host `api.sandbox.webull.com`
  for BOTH `api` and `quotes-api` ($1M paper cash). `us-openapi-alb.uat.webullbroker.com` is a trap (env-mismatch
  401); prod keys work nowhere in UAT. The toolkit's sandbox client was archived 2026-09-29 (`docs/ARCHIVE.md`);
  if revived, keep it out of prod paths with a SEPARATE token dir, never `.webull-tokens/`.
- **`/openapi/config` compat shim.** SDK 2.0.10's `TradeClient`/`DataClient` init probes
  `GET /openapi/config`, which can 404 and raise before the token flow; `client.py` wraps
  `_check_token_enable` to fall back to enabled. **Keep it.**
- **HTTP market-data host = the trade host** (`config.py`). `data-api.*` is MQTT-streaming only
  and times out for HTTP.
- **Market data needs a FREE OpenAPI entitlement** (separate from the app's Nasdaq Basic).
  `get_quote`/`get_snapshot`/`get_bars` return `401 "Insufficient permission…"` until you claim
  **Nasdaq Basic – Non Display** (free) on the OpenAPI side: Webull Technology site → avatar →
  Advanced Quotes → Advanced Quotes Center → **OpenAPI Advanced Quotes**. Level 1 is all we use
  (L2/TotalView $135/mo not needed); ~5–10 min to propagate. `market_data.py` maps the 401 →
  `MarketDataNotEntitledError`. (Options need the separate **OPRA** entitlement,
  $4.99/mo — claimed.)
- **2FA token:** SDK-managed at `conf/token.txt` (or `WEBULL_OPENAPI_TOKEN_DIR`). First creation
  triggers an SMS code in the Webull app; lasts ~15 days, auto-refreshed.
- **Auto-logs:** the SDK writes `*_sdk.log` via `set_file_logger` (relative → CWD);
  `webull_api/sdk_logging.py` reroutes them to absolute `<repo>/logs/<name>-<pid>.log` (patches
  `ApiClient` + `QuotesClient`; applied by `client.py` on import and by the sandbox client, which
  must not import prod config) — this is why MCPs launched from System32 don't fail, and the PID
  suffix is why concurrent processes never collide on log rotation. `logs/` gitignored; nothing
  reads them; `scripts/prune_logs.py` reaps.
- **Rate limits:** order place/cancel/replace 600/min; order detail 2/2s; token 10/30s. Order HISTORY is
  paged and throttled the same way: the SDK's `get_order_history` returns ONE page (`page_size` default 10,
  broker range 10..100, last 7 days) — always read it through `trading.get_order_history` (pages to
  completion, spaced, TOO_MANY_REQUESTS retry; 2026-09-18 — a truncated page once booked a phantom owner flow).
- **`to_ohlcv` is ORDER-AWARE (2026-08-15) — never "simplify" it back to a blind reverse.**
  It reverses only when the series is not provably ascending, making double application a no-op.
  The old unconditional `reverse()` + an injected already-normalized `get_bars` fed the Strategy
  Lab time-reversed bars for 31 cycles (SPY's uptrend tagged "down/low" 31/31; that entire dry
  streak is void — archived at `data/lab/archive-2026-08-15-reversed-bars/`). When injecting a
  bar-fetching closure anywhere, pin the raw-vs-normalized contract with a seam test.
- **Streaming quotes are TYPED objects, not JSON** (verified live 2026-06-15): a **`SnapshotResult`**
  (`.basic.symbol`, `.price/.open/.high/.low/.pre_close/.volume/.change/.change_ratio`, all
  `Decimal`) on the `snapshot` topic and a **`QuoteResult`** (`.asks`/`.bids` of `AskBidResult`
  with `.price`/`.size`) on `quote` — **complementary per symbol**, so `quote_stream.ingest`
  **MERGES** ticks (a quote must not wipe the last snapshot price); `extract` dispatches
  `_fields_from_sdk` (typed) / `_normalize` (dict/JSON, for tests). Re-verify at market hours:
  `.venv/Scripts/python.exe scripts/verify_stream_shape.py`.

## Where things live

> **Public repo note.** The build log, the per-feature design specs and plans, the proof-phase charter,
> the backtest review archive and the trade journals are kept private. Paths in code comments and docs
> that point at `docs/BUILD-LOG.md`, `docs/superpowers/`, `docs/proof-phase-charter.md` or
> `playbook/journal.md` refer to those private files. The `archive/...` git tags named in
> `docs/ARCHIVE.md` live in the private history too.

- **Program map** (the four tracks, their books and caps, where each lives, and the day's timeline): `docs/program-map.md` — rendered from `webull_web/tracks.py` + `routine.py`; run `scripts/render_program_map.py` after editing either.
- **Build history** (every shipped feature, dated, with spec links): `docs/BUILD-LOG.md`.
- **Roadmap / next-up + deferred items:** `docs/ROADMAP.md` (+ project memory).
- **Per-feature specs & plans:** `docs/superpowers/{specs,plans}/`.
- **Operator's guide** ("what I want to do → the exact trigger"): `docs/operator-guide.md`.
- **Claude-managed proof phase:** `docs/proof-phase-charter.md` + `docs/claude-managed-portfolio-review.md`.
- **MCP connectors** (`webull_trade_mcp` + `webull_mcp` + the official `webull`; the paper, strategy, lab and
  day-session MCP wrappers were removed 2026-09-29, `.mcp.json` is empty): read-only/simulated
  **except** `webull-trade`'s codeword `place_order` (the AUTHORIZED EXCEPTION). Layout/setup in the
  `webull-claude-desktop-mcp` memory; configs live in `%APPDATA%\Claude` (restart to apply).

## How to resume

A fresh chat auto-reads this file. The flow used for every feature here is
**superpowers: brainstorming → writing-plans → subagent-driven-development → finishing-a-development-branch**,
then fast-forward merge to `main` + push. **Hard invariant across ALL work:** never weaken the order *submit*
gate — `safety.should_submit` (submission only when `confirm=True`), the dry-run default, and the prod gate
(`WEBULL_ENV=prod` + `confirm=True`) must stay intact. The web arm + typed-CONFIRM path was removed with the
web app 2026-09-28 (owner sign-off; `docs/ARCHIVE.md`).
Two 2026-08-15 additions inside that envelope: the autopilot may CANCEL a resting protective
stop, but ONLY for a gate-allowed closing SELL (`exit:`/`decision:` sources, after `authorize`
allows — clearing before the gate would strip protection from a lot that was never going to be
sold), and `rsi2_real_service` cancels only exit rows whose ids its own ledger recorded;
`webull_web/rsi2_real_service.py` is QUEUE-ONLY (writes executable decisions, never places —
source-guard-tested) and stays OFF behind `WEBULL_RSI2_REAL_ENABLED` until the owner completes
the arming checklist in its spec.
The order *type* surface MAY be widened with explicit user sign-off (e.g. buy-stop / stop-limit were added
2026-06-17, security-reviewed) — but such changes only ADD validation, never loosen the submit rails. So
`safety.py`/`trading.py` are touchable for new order types, but the submit gate within them is sacrosanct.

**Two AUTHORIZED real-order exceptions** to "nothing else places" (all explicit
owner sign-off + security-reviewed; keep intact — when a review/harness flags them as "an MCP places real
orders," this is the *sanctioned* exception, not a regression):
1. **`webull_trade_mcp` `place_order`** (2026-06-18) — env-blind codeword `WEBULL_TRADE_CODEWORD` (model can't
   know it; user types it) → per-order cap `WEBULL_TRADE_MAX_NOTIONAL` ($500) → `trading.place(confirm=True)`;
   opt-in (off unless the codeword is in `.env`). A behavioral test asserts the gate order is unbypassable. All
   other MCPs are place-free (source guards); `draft_order` stays submit-free.
2. **`webull_api/autopilot/` `autopilot_run`** (2026-07-07) — unattended, zero-human-at-submit. A **pure
   fail-closed `gate.authorize`** (enable `WEBULL_AUTOPILOT_ENABLED` → kill-switch → per-order/positions/day/
   loss caps → SPY-regime; risk-reducing SELLs bypass) **ADDS a wall** in front of the byte-untouched
   `should_submit`; **OFF by default**, audit-logged, AST-guarded (`test_gate_hardening.py` proves `run.py`
   places only inside the gated `_try_place`). Go-live = the owner sets the env after a `/security-review`; the
   assistant cannot enable it (harness blocks `confirm=True`).
(Exception #3, the codeword-started day session, was ARCHIVED 2026-09-29 at tag `archive/pre-rsi2-only-2026-09-29`
— never armed; restoring it needs a fresh `/security-review` + owner sign-off.)

**Playbook skills** (`playbook/skills/`): analysis `trade-planner`/`trade-review` + mobile
`journal-capture`/`status-checkin` + the
codeword-gated `trade-placer` (entries) / `exit-placer` (full-close SELLs, exit discipline — a tripped stop is
placed, never lowered/averaged; "sell my X" routes here). **Phone/cloud (mobile) sessions are read + journal +
review only** — never the placer skills, the codeword tool, `autopilot_run`/resume, or autopilot/trade env
changes; the fail-closed kill-file **halt** is the one allowed state change (operator-guide → "Phone"). Specs for both exceptions + the placers live under
`docs/superpowers/specs/` (2026-06-18 order-intent · 2026-07-07 autopilot · 2026-06-2{6,7} placers).
