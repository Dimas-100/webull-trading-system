"""RSI2 mean-reversion PAPER runner — Windows-scheduled entry point (weekdays ~5:00 PM ET). A thin
shim over webull_web.runner_cli.run_cli. 100% paper: never imports trading, never places a real order.

  python scripts/rsi2_paper.py run           # run ONE session (default)
  python scripts/rsi2_paper.py run --force   # ignore the same-day guard
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_web.runner_cli import run_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_cli("rsi2_service", "RSI2", "RSI2 paper runner."))
