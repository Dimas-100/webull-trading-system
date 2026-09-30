# Webull Trade (order intents) — Claude Desktop connector

The order-intent channel (`python -m webull_trade_mcp`). Tools:

- `draft_order(symbol, side, quantity, ...)` — validates + previews an order and writes it to the
  shared `data/intents/` store as a pending intent (JSON only — no confirmation UI since the web app
  was archived 2026-09-28; nothing renders it). Does NOT place; the owner places it (`trade-placer` /
  `exit-placer` with the codeword, or by hand in the Webull app).
- `place_order(symbol, side, quantity, codeword, ...)` — **directly places a REAL order** from chat,
  for small already-decided trades. **OFF unless you set `WEBULL_TRADE_CODEWORD` in `.env`.** Requires
  that secret codeword (only you know it — you type it in chat) and the order must be within the
  per-order cap **`WEBULL_TRADE_MAX_NOTIONAL`** (default $500 = your book). Bigger orders → use `draft_order`
  (there is no automated path to place one — place it by hand in the app).
- `screen_and_draft(watchlist="", symbols="", book=500)` — run your swing screen over a watchlist (or
  explicit symbols) and **draft every PASS** as its DAY buy-stop-limit (does NOT place; skips symbols
  already pending). The owner places each (`trade-placer` with the codeword, or by hand in the Webull app).
- **`manage_exits(account_id="", stop_loss_pct=8.0, take_profit_pct=20.0)`** — scans your open
  positions and DRAFTS a full-close SELL (LIMIT @ last, DAY) for each one that trips an exit signal
  (cost-basis stop, profit target, or a daily close below its 20-day average). Draft-only — the owner
  places it (`exit-placer` with the codeword, or by hand in the Webull app). Skips option positions and
  symbols already drafted.
- **`morning_routine(book=500, account_id="", symbols="", extra_symbols="")`** — the unattended routine in
  ONE call: **DISCOVERS entries by scanning the curated universe** (`discover_and_draft`, NOT a watchlist —
  this account is Claude-managed) + `manage_exits` (exits) + protective stops, drafting everything to
  `data/intents/` with a longer TTL (`WEBULL_ROUTINE_TTL_MIN`, default 240 min) so the drafts survive
  until you review. Draft-only: entries/exits are placed by the owner (`trade-placer` / `exit-placer` with
  the codeword, or by hand in the Webull app); protective stops rest via the armed autopilot's PROTECT stage,
  else the owner places the stop-market by hand in the Webull app — never through the codeword `place_order`
  (it refuses an unpriced STOP_LOSS) and never as a stop-limit (it can fail to fill on a gap-down). Built for
  a Desktop scheduled task.

`place_order` is the ONLY MCP that can place a real order — quarantined here, codeword + cap gated; a
behavioral test asserts `trading.place` is unreachable until both checks pass (no-codeword / wrong-codeword
/ over-cap each refuse with the order never placed). The other two AUTHORIZED real-order exceptions
(`autopilot_run`, the day session) are separate surfaces — see CLAUDE.md.
Credentials + the codeword load from the repo's gitignored `.env`.

## Install in Claude Desktop

1. Claude Desktop → **Settings → Connectors** → **Load unpacked extension**.
2. Point at: `C:\path\to\webull-trading-system\webull_trade_mcp\claude-extension`.
3. Restart Claude Desktop.

## Enable direct placing (optional)

Add to `.env`: `WEBULL_TRADE_CODEWORD=<your-secret>` (and optionally `WEBULL_TRADE_MAX_NOTIONAL=500`).
Without the codeword, `place_order` is inert — `draft_order` still works.

## Scheduled morning routine (Claude Desktop Local task)

Have the day's drafts waiting each morning — no custom scheduler needed. Use Claude Desktop's built-in
**Local** scheduled task (a Remote/cloud routine can't reach this local connector or the local
`data/intents/` store):

1. Claude Desktop → **Routines** → **New routine** → choose **Local** (on this device), NOT Remote.
2. **Schedule:** Weekdays, ~**8:00 AM ET** (pre-open — uses the prior session's complete daily bar).
3. **Working folder:** the repo (`C:\path\to\webull-trading-system`).
4. **Instruction:** *"Call the `morning_routine` tool, then give me a one-paragraph summary of what was
   drafted (entries and exits) and why."*
5. **Permissions:** allow the `morning_routine` tool — it is **draft-only**. (It can never place a real
   order: `place_order` is codeword-gated and the model doesn't know the codeword.)

The drafts land in `data/intents/` (JSON, no display surface) with the 4-hour TTL; review each. Entries
and exits: the owner places them via `trade-placer` / `exit-placer` (codeword) or by hand in the Webull app.
Protective stops: the armed autopilot's PROTECT stage rests them, else the owner places the stop-market by
hand in the Webull app (never a stop-limit).
