"""`python -m webull_trade_mcp` — run the order-intent channel MCP over stdio.

Reuses webull_mcp.env.load_repo_env() for the hardened startup (repo .env; SDK logging off stdout;
chdir to repo for conf/+logs/legacy relative overrides — the order-intent store itself is
repo-anchored via webull_api/paths.py::data_dir, under data/intents/).
"""
from __future__ import annotations

from webull_mcp.env import load_repo_env


def main() -> None:
    load_repo_env()
    from .server import mcp  # imported after env load

    mcp.run()


if __name__ == "__main__":
    main()
