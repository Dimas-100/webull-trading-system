"""webull_trade_mcp — the order-intent channel MCP.

draft_order writes a validated, previewed order *intent* to the shared data/intents/ store (JSON
only -- no confirmation surface since the web app was archived 2026-09-28). place_order (opt-in) is
the ONE quarantined direct real-order surface, gated by a secret .env codeword the model can't see +
a per-order $ cap. Reuses webull_mcp.env for the hardened, cwd-independent startup.
"""
