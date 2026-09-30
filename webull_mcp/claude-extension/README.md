# Webull Options Data — Claude Desktop connector

A read-only option-market-data MCP (`python -m webull_mcp`) for Claude Desktop. It exposes:

- `get_option_expirations(symbol)` — live expirations + spot for an underlying.
- `get_option_chain(symbol, expiration, width=12)` — strikes × call/put with last, bid/ask,
  implied volatility, greeks, open interest.
- `get_option_snapshot(occ_symbols)` — quote(s) for specific OCC contracts (≤20, comma-separated).

**Credentials** load from this repo's gitignored `.env` (via `webull_mcp.env.load_repo_env`) — the
manifest stores **no** App Key/Secret. Requires the OPRA (US_OPTION) market-data entitlement on the
account.

## Install in Claude Desktop

This dir is a ready-to-load **unpacked** DXT extension. If it didn't appear automatically after a
restart, load it manually:

1. Claude Desktop → **Settings → Connectors**.
2. Enable developer mode if needed, then **Load unpacked extension** (the `+`).
3. Point at this folder: `C:\path\to\webull-trading-system\webull_mcp\claude-extension`.
4. Restart Claude Desktop. The **Webull Options Data** connector should list the three tools above.

The manifest's paths are machine-specific (this repo's `.venv` python + `PYTHONPATH`); adjust if the
repo moves.
