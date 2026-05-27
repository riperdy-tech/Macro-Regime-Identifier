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

& python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "News accumulation failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "News accumulation report failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli build-secular-theme-scores --config config/news_scoring.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Secular theme tracker failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli export-dashboard-data *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Dashboard export failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli write-automation-summary *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Automation summary failed. See $LogPath"
    exit $LASTEXITCODE
}

Write-Host "Daily diagnostic completed. See $LogPath"
exit 0
