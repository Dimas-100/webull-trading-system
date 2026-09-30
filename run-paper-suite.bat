@echo off
REM  Run the WHOLE paper suite (every code-backed paper runner in order — the list lives in
REM  webull_web\runner_cli.py PAPER_SUITE) in one weekday-EOD session.
REM  Target of the "Webull Paper Suite" Windows Task Scheduler weekday job (~5:30 PM ET). The nightly note is
REM  NOT in it (run-nightly-note.bat, 18:25, after the autopilot backstop).
REM  Each runner writes its own run-log, so every Monitor cadence row goes green from this one job.
REM  Append-logs to logs\paper_suite.log. Paper-only — imports no trading path, places NO real orders.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Set up the virtualenv first.
  exit /b 1
)
if not exist "logs" mkdir logs

.venv\Scripts\python.exe scripts\run_paper_suite.py >> logs\paper_suite.log 2>&1
