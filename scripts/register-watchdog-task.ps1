# Register the "Webull Suite Watchdog" scheduled task — the dead-man's alert for the
# evening paper suite. Run it yourself, or give the assistant the go-ahead to run it:
#   powershell -ExecutionPolicy Bypass -File scripts\register-watchdog-task.ps1
#
# Weekdays 7:00 PM local (the 5:30 suite + 5:45 autopilot are long done), wakes the PC,
# runs as the logged-on user. Alert-only and read-only: it reads the run-log and pushes
# ntfy when the suite is dead/degraded; it never places, cancels, or modifies anything.
# Limitation: a powered-OFF PC cannot alert about itself in the evening; StartWhenAvailable
# + the service's look-back mode back-alert at the next boot, and the external healthchecks
# dead-man (WEBULL_WATCHDOG_HEALTHCHECK_URL) covers a PC that never comes back.
# Pause/resume: Disable-/Enable-ScheduledTask -TaskName "Webull Suite Watchdog"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\python.exe" `
    -Argument "$repo\scripts\suite_watchdog.py" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "19:00"
$trigger.StartBoundary = (Get-Date -Format "yyyy-MM-dd") + "T19:00:00"   # plain LOCAL time: an offset-bearing boundary (what New-ScheduledTaskTrigger writes) fires an hour early once DST ends
# -RunOnlyIfNetworkAvailable: a boot-time catch-up firing before Wi-Fi associates would
# push-fail and still consume its one shot, so the network condition defers it instead.
$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
# -Force: the task already exists from the first registration; without it this documented
# re-register step throws "Cannot create a file when that file already exists" and the new
# settings above silently never apply.
Register-ScheduledTask -TaskName "Webull Suite Watchdog" -Action $action `
    -Trigger $trigger -Settings $settings -Force `
    -Description "Dead-man's watchdog: pushes ntfy when the evening paper suite did not report (reads the run-log; alert-only, read-only). Suite runs 5:30 PM; this checks at 7:00 PM."

Get-ScheduledTask -TaskName "Webull Suite Watchdog" | Format-List TaskName, State, Description
(Get-ScheduledTask -TaskName "Webull Suite Watchdog").Triggers | Format-List StartBoundary, DaysOfWeek
Write-Host "`nRegistered. First fire: next weekday at 7:00 PM. Silence = healthy; a push = act."
