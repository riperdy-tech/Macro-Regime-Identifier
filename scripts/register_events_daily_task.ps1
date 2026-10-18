# register_events_daily_task.ps1 - WOULD register one Windows Scheduled Task, "MRI-Events-Daily", that runs
# scripts/run_events_daily.ps1 every day at 08:30 Korea Standard Time (operator decision D3: a morning update, not intraday).
# NOT RUN by the build that wrote it: registering waits for the operator's ruling on who owns local news hydration
# (docs/events_build/STAGE1_RUNBOOK.md, "Open decision").
#
# Time zone: the task trigger is a local clock time. 08:30 KST is converted to this machine's local zone with
# [TimeZoneInfo] for TODAY's date, and that local time of day is used. On a machine set to KST it is 08:30. On a machine
# in a zone with daylight saving the local time of day shifts with the clock change while KST does not: re-run this script
# after a clock change if that matters.
#
#   powershell -ExecutionPolicy Bypass -File scripts/register_events_daily_task.ps1            register
#   powershell -ExecutionPolicy Bypass -File scripts/register_events_daily_task.ps1 -Remove    unregister (roll back)
param([switch]$Remove, [string]$TimeKst = "08:30")

$TaskName = "MRI-Events-Daily"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed $TaskName"
    return
}
$RepoRoot = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")).Path
$Script = Join-Path $RepoRoot "scripts\run_events_daily.ps1"

$Kst = [TimeZoneInfo]::FindSystemTimeZoneById("Korea Standard Time")
$Parts = $TimeKst.Split(":")
$KstToday = [DateTime]::SpecifyKind((Get-Date).Date.AddHours([int]$Parts[0]).AddMinutes([int]$Parts[1]), [DateTimeKind]::Unspecified)
$Local = [TimeZoneInfo]::ConvertTime($KstToday, $Kst, [TimeZoneInfo]::Local)
$First = (Get-Date).Date + $Local.TimeOfDay
# the first run is the NEXT occurrence: a start time already passed today would fire at once under -StartWhenAvailable
if ($First -le (Get-Date)) { $First = $First.AddDays(1) }

$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $RepoRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $First
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "MRI events: hydrate local news, then build the daily candidate register (logs in logs/events)"
Write-Output ("registered $TaskName daily at " + $Local.ToString("HH:mm") + " local (= $TimeKst KST) -> $Script")
