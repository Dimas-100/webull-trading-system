@echo off
REM ============================================================
REM  Daily Tiingo EOD backfill (incremental; feeds the bench + Lab stores).
REM  Target of the "Webull Tiingo Backfill" Task Scheduler job,
REM  weekdays 18:35 local (moved 2026-09-18; no wake-to-run). Ends with
REM  the day-trade universe refresh and the lab-candidate paper stage
REM  (scriptsench.py paper, ~18:40).
REM  Append-logs to logs\tiingo_backfill.log. Offline data only -
REM  never touches the trading path.
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe not found. Set up the virtualenv first.
  exit /b 1
)
if not exist "logs" mkdir logs

.venv\Scripts\python.exe scripts\tiingo_backfill.py --all-curated >> logs\tiingo_backfill.log 2>&1
REM  SIMPLIFIED 2026-09-29 (owner: RSI2-only; Tiingo on the FREE tier): the day-trade universe,
REM  session-grid pool/watchlist and bench paper stage steps were removed. Restore from git history.
exit /b 0
