# register_health_watch_task.ps1 - one Windows Scheduled Task, "MRI-Health-Watch", that runs scripts/run_health_watch.ps1
# twice a day, each time after a job it watches: 07:20 local (after MRI-Local-Daily, 06:30) and 14:10 local (after
# MRI-Events-Daily, 13:00). WO_MA1. Written, not registered: the operator runs it.
#
#   powershell -ExecutionPolicy Bypass -File scripts/register_health_watch_task.ps1            register
#   powershell -ExecutionPolicy Bypass -File scripts/register_health_watch_task.ps1 -Remove    unregister
param([switch]$Remove)

$TaskName = "MRI-Health-Watch"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed $TaskName"
    return
}
$RepoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")
$Script = Join-Path $RepoRoot "scripts\run_health_watch.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $RepoRoot
# each first run is the NEXT occurrence: a start time already passed today would fire at once under -StartWhenAvailable
function NextAt([int]$hour, [int]$minute) {
    $t = (Get-Date).Date.AddHours($hour).AddMinutes($minute)
    if ($t -le (Get-Date)) { $t = $t.AddDays(1) }
    return $t
}
$Triggers = @((New-ScheduledTaskTrigger -Daily -At (NextAt 7 20)), (New-ScheduledTaskTrigger -Daily -At (NextAt 14 10)))
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Triggers -Settings $Settings `
    -Description "MRI health watch: checks the daily jobs and their outputs and sends a heartbeat to GitHub (alert on failure); logs in logs/health_watch"
Write-Output "registered $TaskName daily 07:20 and 14:10 -> $Script"
