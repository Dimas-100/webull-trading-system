# Register the "Webull Autopilot" scheduled task — the go-live step.
# Run this YOURSELF (the assistant is deliberately blocked from doing it):
#   powershell -ExecutionPolicy Bypass -File scripts\register-autopilot-task.ps1
#
# Weekdays 5:45 PM local (inside the 17:00-20:00 gate window, after the 5:30 paper
# suite), wakes the PC, runs as the logged-on user. One gated cycle per fire:
# reconcile -> protect -> exits -> entries; every decision lands in
# autopilot\log\<date>.jsonl. Emergency stop any time: autopilot.bat halt
# Pause/resume the schedule: Disable-/Enable-ScheduledTask -TaskName "Webull Autopilot"

$repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$action = New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\python.exe" `
    -Argument "$repo\scripts\autopilot.py run" -WorkingDirectory $repo
$trigger = New-ScheduledTaskTrigger -Weekly `
    -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "17:45"
$trigger.StartBoundary = (Get-Date -Format "yyyy-MM-dd") + "T17:45:00"   # plain LOCAL time: an offset-bearing boundary (what New-ScheduledTaskTrigger writes) fires an hour early once DST ends
$settings = New-ScheduledTaskSettingsSet -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName "Webull Autopilot" -Action $action `
    -Trigger $trigger -Settings $settings `
    -Description "Claude-managed autopilot: one gated cycle (reconcile-protect-exits-entries) each weekday evening inside the 17:00-20:00 window. Caps + kill-file in webull_api/autopilot. Halt: autopilot.bat halt"

Get-ScheduledTask -TaskName "Webull Autopilot" | Format-List TaskName, State, Description
(Get-ScheduledTask -TaskName "Webull Autopilot").Triggers | Format-List StartBoundary, DaysOfWeek
Write-Host "`nRegistered. First fire: next weekday at 5:45 PM. Watch the first one if you can."
