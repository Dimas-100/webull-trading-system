# Registers the "Webull Prune Logs" scheduled task: weekdays 18:50 local, runs scripts\prune_logs.py
# (deletes logs\ files older than 14 days; trims the append-forever suite logs above 5 MB to their
# last 1 MB). Idempotent: re-registers if present. Runs as the registering interactive user (no
# elevation). Reads nothing the app uses and touches no trading path.
#
# 18:50 sits inside the evening window the PC is already awake for (18:35 Tiingo backfill,
# 19:00 watchdog). No -WakeToRun on purpose: on this PC a wake-to-run task freezes when Modern
# Standby re-enters in the same second (see register-bench-feeder-task.ps1, 2026-09-18);
# StartWhenAvailable runs a missed start at the next REAL wake instead. StartBoundary is plain
# local time (an offset-bearing boundary fires an hour early once DST ends).
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$name = "Webull Prune Logs"
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\python.exe" `
    -Argument "scripts\prune_logs.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "18:50"
$trigger.StartBoundary = (Get-Date -Format "yyyy-MM-dd") + "T18:50:00"   # plain LOCAL time (DST-safe)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
}
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Prunes <repo>\logs\: deletes rotated SDK logs older than 14 days and trims the append-forever suite logs above 5 MB to their last 1 MB (scripts\prune_logs.py). Reads nothing the app uses; touches no trading path. Weekdays 18:50 local (no wake-to-run; StartWhenAvailable)." | Out-Null
$t = Get-ScheduledTask -TaskName $name
"{0}  {1}" -f $t.TaskName, $t.State
$t.Triggers | ForEach-Object { "trigger {0} {1}" -f $_.StartBoundary, ($_.DaysOfWeek -join ',') }
