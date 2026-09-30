"""webull_mcp — a read-only option-market-data MCP server over the webull_api toolkit.

Exposes option expirations / chain / contract snapshots to MCP clients (e.g. Claude Desktop).
Read-only by construction: it never imports the order-placing surface (see tests/test_webull_mcp.py).
Credentials come from the repo's gitignored .env (loaded by webull_mcp.env) — never from the MCP
client config.
"""
