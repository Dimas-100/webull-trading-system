"""Run the REAL-book RSI2 runner (queues executable decisions; never places)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webull_web.runner_cli import run_cli  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_cli("rsi2_real_service", "RSI2-real",
                             "Queue real-book RSI2 decisions (OFF unless WEBULL_RSI2_REAL_ENABLED)"))
