# TradingView — experimental read-and-alert layer for the paper day session

> **Status 2026-09-21: EXPERIMENTAL.** TradingView's official MCP server is a watching aid for the session-grid
> paper session. It is read-only plus alerts. It never places an order (the server has no broker tools), never
> feeds the real rails, and never becomes the source of the ticket — the ticket comes from
> `scripts/session_grid_live.py`; TradingView is a cross-check and a chart. S/R as a *mechanical* rule has already
> failed here (ORB's resistance filter, `pdh_break` in the 2026-09-20 grid); the lines are for watching, not trading.

## The server

| | |
|---|---|
| URL | `https://mcp.tradingview.com/mcp` |
| Auth | OAuth sign-in with the TradingView account (public beta, Essential plan or higher) |
| Tools (beta, limited) | quotes + price history (**delayed** in beta), Screener, alerts (create/manage), watchlists, fundamentals, news, SEC filings / transcripts, economic calendar |
| Not there | broker orders. The Webull link on the chart is chart-trading by hand only; the MCP cannot reach it. |
| Beta caveats | delayed market data, a possible per-day request cap, tool list may change |

## Step 1 — connect it (the owner does the OAuth)

1. claude.ai → **Settings → Connectors → Add custom connector**. Name `TradingView`, remote MCP server URL
   `https://mcp.tradingview.com/mcp`, leave the OAuth client fields blank, **Add**.
2. Click **Connect** on the new card → TradingView's sign-in page → approve → back on claude.ai the card reads
   *Connected*.
3. In a claude.ai chat, open the tools menu (the connectors icon next to the message box) and make sure
   TradingView is toggled on. Ask "list your TradingView tools" to see the exact tool set the beta exposes.
4. **Claude Code — the route taken 2026-09-21 (direct, no claude.ai connector needed):**
   `claude mcp add --transport http mcp-tradingview https://mcp.tradingview.com/mcp` (registered in the local
   scope of this project, `~/.claude.json`). The endpoint answers `401` with a `WWW-Authenticate` OAuth challenge
   (resource metadata at `/.well-known/oauth-protected-resource/mcp`), so the health check reads *Failed to connect*
   until you sign in: inside Claude Code run **`/mcp`**, pick `mcp-tradingview`, choose **Authenticate** — the browser
   opens TradingView's consent page; approve. Then start a new session (or reconnect from `/mcp`); the tools appear as
   `mcp__mcp-tradingview__*`. The assistant then reports the tool list, the plan tier the server reports, and whether
   quotes are flagged delayed. (Alternative: the claude.ai connector above; those load as `claude.ai <Name>` servers
   at session start, e.g. the existing Webull/Gmail/Vercel ones.) Remove with
   `claude mcp remove mcp-tradingview -s local`.

## Step 2 — the four read-only experiments (in order)

1. **Bars:** TradingView 1-minute bars for the ticket's symbol vs our bars for the same session — VWAP through
   09:44, 09:30–09:44 volume, 09:45 price (close of the 09:44 bar). "Our" side has two feeds: the Webull M1 bars the
   live screen actually used (consolidated volume) and the Tiingo **IEX** minute store (IEX prints only, so its
   volume is a fraction of consolidated). Refresh the IEX store for the symbol first:
   `python scripts/tiingo_intraday_backfill.py --symbols SYM --start 2026-09-01 --end <today>`.
2. **Screener:** TradingView's biggest movers from the open as of 09:45 vs our screen's rank
   (`data/day_trade/session-grid-<date>.log` prints the winner; on a no-signal day the top 3). Cross-check only.
3. **Alerts by hand** at the ticket's entry, stop and 10:58 flat — below.
4. **Levels on the chart** — the Pine indicator below.

Results go in a dated memo under `docs/reviews/` and the `tradingview-mcp-setup` memory.

## Alerts by hand on the Webull-linked chart (experiment 3)

Alerts belong to the chart, not the broker panel, so the Webull link changes nothing. Three alerts per ticket, set
the moment the ticket prints (09:49–09:52), all expiring at 11:05 the same day.

**Entry and stop — price alerts (fastest):**
1. Open the symbol on a 1-minute chart. Right-click the chart **at the entry price** (or hover the price scale and
   click the `+`) → *Add alert on SYM at <price>*.
2. In the dialog: Condition **SYM · Crossing · <entry limit>**. Trigger **Only Once**. Expiration **today 11:05**.
   Name `SG entry SYM <limit>`. Notifications: *Notify in app* + *Push* (mobile). **Create**.
3. Repeat for the stop with **Crossing Down · <stop>**, name `SG stop SYM <stop>`.

**10:58 flat — a clock alert, which a price line cannot do:**
1. Add the *Session Grid Levels* indicator (below) to the chart, put the ticket's entry and stop in its inputs.
2. `Alt+A` → Condition: **Session Grid Levels** → pick **SG 10:58 flat** → Trigger **Once Per Bar** → Expiration
   today 11:05 → Create. The same menu offers *SG entry crossed*, *SG stop broken* and *SG 09:55 cancel* if you
   prefer all four from one place.

**Through the MCP (once connected):** in claude.ai, ask for the same three alerts by name and level — the alerts
tool creates them on the account, the chart shows them. Verify the first one by hand before trusting it.

Rules that stay: the machine trades the paper book on its own timeline (10:58 flat, 11:03 hard end); an alert is a
prompt to *watch*, never to touch the machine's symbol in the paper account while a session runs.

## Levels on the chart (experiment 4) — `session-grid-levels.pine`

Paste into Pine Editor → *Add to chart*. Inputs: **Entry** (the ticket's BUY limit) and **Stop** (0 hides the line).
Draws, per day, in New York time:

| Line | What | Matches |
|---|---|---|
| PDH / PDL (grey, dashed) | prior-day RTH high / low from the daily series | the screen's `pdh` |
| PMH / PML (violet, dotted) | pre-market 04:00–09:29 high / low — **chart must show Extended Hours** or these stay blank | — |
| OR15 H / L (sky) | 09:30–09:44 opening range; the low is the ticket's *structure* | `or15_h` / `or15_l` |
| VWAP (amber) | session VWAP from 09:30, hlc3, RTH bars only | the screen's `_vwap` |
| ENTRY / STOP (green / red) | the ticket | — |
| clock (slate, vertical) | 09:45 check · 09:55 unfilled-cancel · 10:58 machine flat · 11:00 rule flat | the runbook |

Known difference to keep in mind: TradingView's **built-in** VWAP anchors at 04:00 when extended hours are shown, so
it sits away from this one early in the session; the indicator's VWAP is the screen's definition.
