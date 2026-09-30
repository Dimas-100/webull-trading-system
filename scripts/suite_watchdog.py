"""Dead-man's watchdog for the evening paper suite: alerts (ntfy) when the suite did not
report today. A thin shim over webull_web.watchdog_service. Read-only + push; never
imports trading, never places an order. Scheduled as "Webull Suite Watchdog" (weekdays
7:00 PM — register-watchdog-task.ps1); safe to run by hand any time:

  python scripts/suite_watchdog.py           # weekday 6:30 PM+ ET: assesses today.
                                              # Pre-window or weekend: looks BACK instead of
                                              # no-opping - can push a back-alert and exit 1.
  python scripts/suite_watchdog.py --force   # ignore the weekday/window guards; assess today now
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_web.watchdog_service import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
