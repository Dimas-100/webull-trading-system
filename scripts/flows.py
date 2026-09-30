"""Owner cash-flow detection runner — Windows-scheduled entry point (part of the paper suite).
A thin shim over webull_web.runner_cli.run_cli. Broker READS only (order history) + a
contributions-ledger append: never the order gate.

  python scripts/flows.py run           # run ONE session (default)
  python scripts/flows.py run --force   # ignore the same-day guard
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_web.runner_cli import run_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_cli("flows_service", "Flows", "Owner cash-flow detection runner."))
