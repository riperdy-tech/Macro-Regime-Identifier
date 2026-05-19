$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs\daily"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "daily_diagnostic_$Timestamp.log"

$Args = @(
    "-m",
    "macro_engine.cli",
    "run-daily-diagnostic",
    "--config",
    "config/daily_pipeline.yaml",
    "--archive"
)

if ($env:MACRO_ENGINE_LIVE_AI -eq "1") {
    $Args += "--live-ai"
} else {
    $Args += "--mock-ai"
}

if ($env:MACRO_ENGINE_SOURCE_PROFILE) {
    $Args += @("--source-profile", $env:MACRO_ENGINE_SOURCE_PROFILE)
}

Write-Host "Running daily diagnostic. Log: $LogPath"
& python @Args *> $LogPath
$ExitCode = $LASTEXITCODE

if ($ExitCode -ne 0) {
    Write-Host "Daily diagnostic failed. See $LogPath"
    exit $ExitCode
}

Write-Host "Daily diagnostic completed. See $LogPath"
exit 0
