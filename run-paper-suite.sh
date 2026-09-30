#!/usr/bin/env bash
# Run the WHOLE paper suite once (every code-backed paper runner, in order; the list lives in
# webull_web/runner_cli.py PAPER_SUITE) — the cross-platform
# sibling of run-paper-suite.bat, for a dedicated always-on host (Linux/macOS via cron or systemd).
# 100% paper: imports no trading path, places NO real orders. Append-logs to logs/paper_suite.log.
#
# Cron (host-local time — fire AFTER ~4:30 PM ET so the daily bar is settled; the runners no-op if it
# isn't, so an off-by-a-bit time is safe): e.g. weekdays 5:30 PM if the host is ET:
#     30 17 * * 1-5  cd /path/to/webull && ./run-paper-suite.sh
set -euo pipefail
cd "$(dirname "$0")"

# Prefer a POSIX venv; fall back to a Windows-layout venv (e.g. running this under Git Bash on Windows).
PY="./.venv/bin/python"
if [ ! -x "$PY" ]; then PY="./.venv/Scripts/python.exe"; fi
if [ ! -x "$PY" ]; then
  echo "ERROR: no .venv python found (looked in .venv/bin and .venv/Scripts). Set up the venv first." >&2
  exit 1
fi

mkdir -p logs
"$PY" scripts/run_paper_suite.py "$@" >> logs/paper_suite.log 2>&1
