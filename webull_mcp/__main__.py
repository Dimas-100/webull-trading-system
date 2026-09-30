"""`python -m webull_mcp` — run the read-only option-market-data MCP over stdio.

Loads the repo's .env first (cwd-independent) so Webull credentials are available, then starts
the FastMCP stdio server.
"""
from __future__ import annotations

from .env import load_repo_env


def main() -> None:
    load_repo_env()
    from .server import mcp  # imported after env load

    mcp.run()


if __name__ == "__main__":
    main()
