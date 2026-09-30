"""Run the Webull runner suite in one weekday-EOD session — the steps in webull_web.runner_cli.PAPER_SUITE,
in order: netliq_snapshot -> rsi2_real (REAL-book RSI2, queue-only) -> flows (owner deposit/withdrawal
detection); the nightly note is its own later task (scripts/nightly_note.py). A thin shim over webull_web.runner_cli.run_suite. Never places an order — the
rsi2_real step only QUEUES decisions for the gated autopilot executor, and the flows step's broker reads are
order history only. (RSI2-only since 2026-09-29; the other old steps are archived — docs/ARCHIVE.md.)

  python scripts/run_paper_suite.py           # run the suite (default)
  python scripts/run_paper_suite.py --force   # ignore each runner's same-day guard
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_web.runner_cli import run_suite  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_suite())
