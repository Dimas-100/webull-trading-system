# Sets the "Webull Autopilot" morning trigger to 09:31 ET on weekdays and pins EVERY trigger on
# the task to plain local time. Idempotent - safe to re-run; adds the morning trigger if missing.
#
# Why 09:31 (owner, 2026-09-13): the queued RSI2 exits should sell at the open, not five minutes
# after it. Not 09:30:00 - the run submits ~10-15 s after the trigger and cancels the resting stop
# BEFORE the MARKET sell; a gateway still outside the regular session would 417 the sell and leave
# the lot unprotected (the 2026-08-18 shape). One minute of margin removes that boundary.
#
# ORDER MATTERS: the gate window in .env (WEBULL_AUTOPILOT_WINDOWS) must already start at 09:31.
# The executor consumes a confirmed exit's single-use confirmation BEFORE the gate runs, so a
# window-denied morning run burns the confirmation and the exit slips a day. Edit .env FIRST.
#
# Why local time: New-ScheduledTaskTrigger writes a UTC-anchored StartBoundary ("synchronize
# across time zones"), so every trigger registered that way fires an hour EARLY on the clock once
# DST ends (2026-11-01) - the morning and 17:45 runs would land outside the gate window. A
# StartBoundary without an offset is plain local time and follows the clock.
#
# Run as the task's owner:
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\set-autopilot-morning-trigger.ps1
# Preview without changing anything:            ... -DryRun
# Pin another task's triggers to local time only (no morning retime):
#   ... -TaskName "Webull IBS Book Paper" -PinOnly
param(
    [string]$TaskName = "Webull Autopilot",
    [string]$MorningAt = "09:31",
    [string]$OldMorning = "09:35",
    [switch]$PinOnly,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName
$triggers = @()
$changes = @()
$seen = @{}

foreach ($t in @($task.Triggers)) {
    $sb = [string]$t.StartBoundary          # 2026-08-15T09:35:00-04:00  or  2026-07-10T17:30:00
    if ($sb.Length -lt 19) { throw "$TaskName - unexpected StartBoundary '$sb'" }
    $day  = $sb.Substring(0, 10)
    $hhmm = $sb.Substring(11, 5)            # the wall-clock time the owner registered
    if (-not $PinOnly -and $hhmm -eq $OldMorning) {
        $changes += "retimed the $OldMorning trigger to $MorningAt"
        $hhmm = $MorningAt
    }
    if ($seen.ContainsKey($hhmm)) {
        $changes += "dropped a duplicate $hhmm trigger"
        continue
    }
    $seen[$hhmm] = $true
    $pinned = "${day}T${hhmm}:00"
    if ($sb -ne $pinned) {
        if ($sb -match '(Z|[+-]\d{2}:\d{2})$') { $changes += "pinned $hhmm to local time (was $sb)" }
        $t.StartBoundary = $pinned
    }
    $triggers += $t
}

if (-not $PinOnly -and -not $seen.ContainsKey($MorningAt)) {
    $new = New-ScheduledTaskTrigger -Weekly `
        -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $MorningAt
    $new.StartBoundary = (Get-Date -Format 'yyyy-MM-dd') + "T${MorningAt}:00"
    $triggers += $new
    $changes += "added the $MorningAt weekday trigger"
}

if ($changes.Count -eq 0) {
    Write-Host "$TaskName - nothing to do (morning trigger already $MorningAt; every trigger local)."
} elseif ($DryRun) {
    Write-Host "$TaskName - DRY RUN, would apply:"
    $changes | ForEach-Object { Write-Host "  $_" }
    Write-Host "  resulting StartBoundary list:"
    $triggers | ForEach-Object { Write-Host "    $($_.StartBoundary)  days=$($_.DaysOfWeek)" }
} else {
    Set-ScheduledTask -TaskName $TaskName -Trigger $triggers | Out-Null
    $changes | ForEach-Object { Write-Host "$TaskName - $_" }
}

if (-not $DryRun) {
    (Get-ScheduledTask -TaskName $TaskName).Triggers |
        Format-Table StartBoundary, DaysOfWeek, Enabled -AutoSize
    Write-Host ("next run: " + (Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo).NextRunTime)
}
