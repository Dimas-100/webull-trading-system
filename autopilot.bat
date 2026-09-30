@echo off
REM Autopilot operator console. Usage: autopilot [status|run|halt|resume]  (default: status)
REM %~dp0 = this file's dir (repo root) so it works from any CWD.
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\autopilot.py" %*
