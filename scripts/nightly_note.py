"""Compose, store and push the nightly note — ONE step, run after the 18:15 autopilot backstop so the
protective stops the evening autopilot run places are in it (task "Webull Nightly Note", weekdays 18:25).
A thin shim over webull_web.runner_cli.run_suite(runners=NOTE_STEP): same env load, EOD-window guard (a
boot catch-up before 16:00 ET is a no-op that stamps nothing) and network gate as the evening suite.
Never places an order — the note reads files and broker open orders only.

  python scripts/nightly_note.py           # run the note (default)
  python scripts/nightly_note.py --force   # ignore the same-day guard / EOD window
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root on path

from webull_web.runner_cli import NOTE_STEP, run_suite  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(run_suite(runners=NOTE_STEP))
