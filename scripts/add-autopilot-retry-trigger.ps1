# Adds the 18:15 ET weekday RETRY trigger to the "Webull Autopilot" task (idempotent).
# Why: the 17:45 run is the ONLY one that can act on rsi2_above exits (the daily bar publishes
# post-close), so a transient failure there costs a full exit day — live 2026-08-18, when a
# single 429 on the clear_stop open-orders read denied the AAPL exit with RSI(2) at 90.5.
# The run is idempotent (acted-token replay wall, dedup, caps) and a denied decision keeps a
# retryable deny: token, so this pass is a no-op whenever 17:45 succeeded.
# Run as the task's owner:  powershell -NoProfile -ExecutionPolicy Bypass -File scripts\add-autopilot-retry-trigger.ps1
$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName "Webull Autopilot"

$already = $task.Triggers | Where-Object { $_.StartBoundary -match "T18:15" }
if ($already) {
    Write-Host "18:15 trigger already present - nothing to do."
} else {
    $new = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "18:15"
    $new.StartBoundary = (Get-Date -Format "yyyy-MM-dd") + "T18:15:00"   # plain LOCAL time (see set-autopilot-morning-trigger.ps1: offset-bearing boundaries shift an hour at DST)
    Set-ScheduledTask -TaskName "Webull Autopilot" -Trigger @($task.Triggers + $new) | Out-Null
    Write-Host "18:15 weekday retry trigger added."
}

(Get-ScheduledTask -TaskName "Webull Autopilot").Triggers |
    Format-List StartBoundary, DaysOfWeek
