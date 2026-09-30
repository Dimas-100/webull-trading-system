@echo off
REM  Compose + push the nightly note (webull_web\manager_note_service via runner_cli.NOTE_STEP).
REM  Target of the "Webull Nightly Note" Windows Task Scheduler weekday job (18:25 ET) — AFTER the
REM  18:15 autopilot backstop, so tonight's protective stops are in the note.
REM  Append-logs to logs\nightly_note.log. Imports no trading path, places NO orders.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Set up the virtualenv first.
  exit /b 1
)
if not exist "logs" mkdir logs

.venv\Scripts\python.exe scripts\nightly_note.py >> logs\nightly_note.log 2>&1
