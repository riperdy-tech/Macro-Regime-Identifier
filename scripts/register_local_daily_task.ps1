# register_local_daily_task.ps1 - one Windows Scheduled Task, "MRI-Local-Daily", that runs scripts/run_local_daily.ps1
# every day at 06:30 local time: after the screener's PC run has pushed its data (~01:40) and well before its next
# run reads MRI (~21:35). Operator approval: option A, 2026-10-04.
#
#   powershell -ExecutionPolicy Bypass -File scripts/register_local_daily_task.ps1            register
#   powershell -ExecutionPolicy Bypass -File scripts/register_local_daily_task.ps1 -Remove    unregister
param([switch]$Remove)

$TaskName = "MRI-Local-Daily"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed $TaskName"
    return
}
$RepoRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")
$Script = Join-Path $RepoRoot "scripts\run_local_daily.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`"" -WorkingDirectory $RepoRoot
# the first run is the NEXT 06:30: a start time already passed today would fire at once under -StartWhenAvailable
$First = (Get-Date).Date.AddHours(6.5)
if ($First -le (Get-Date)) { $First = $First.AddDays(1) }
$Trigger = New-ScheduledTaskTrigger -Daily -At $First
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "MRI daily run on this PC: macro, sector, cost-of-capital anchor and shocks (no news); logs in logs/local_daily"
Write-Output "registered $TaskName daily 06:30 -> $Script"
