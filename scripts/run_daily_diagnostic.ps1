$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

# Fail fast if the venv's editable install resolves `macro_engine` outside this repo (a stale
# .pth after a reorg silently stopped the daily diagnostic for four months last time).
& python (Join-Path $ScriptDir "check_macro_engine_import.py")
if ($LASTEXITCODE -ne 0) {
    Write-Host "Environment check failed; see the message above. Daily diagnostic did not run."
    exit $LASTEXITCODE
}

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

& python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "News source coverage report failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli build-secular-theme-scores --config config/news_scoring.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Secular theme tracker failed. See $LogPath"
    exit $LASTEXITCODE
}

# S1.7 / S0.7a: the report writer is a separate step from the builder, so a stale report can
# silently disagree with the store (MRI_S0_APPROVAL.md §1 row 14 caught `sector_validation.json`
# doing exactly this). Both run every day so neither can go stale between runs.
& python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Sector validation failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Sector validation report failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli run-nber-benchmark --benchmark-config config/nber_recessions.yaml *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "NBER benchmark failed. See $LogPath"
    exit $LASTEXITCODE
}

& python -m macro_engine.cli write-regime-status *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Regime status failed. See $LogPath"
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

& python -m macro_engine.cli export-dashboard-data *> $LogPath -Append
if ($LASTEXITCODE -ne 0) {
    Write-Host "Final dashboard export failed. See $LogPath"
    exit $LASTEXITCODE
}

Write-Host "Daily diagnostic completed. See $LogPath"
exit 0
