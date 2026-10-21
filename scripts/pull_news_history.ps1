# N1.4: pull the durable news-history snapshot from the `run-history` branch and
# hydrate it into the LOCAL store. Insert-if-absent, precedence-protected -- the
# 207 real local classifications can never be displaced. Data flows one way,
# cloud -> run-history -> local; this script never pushes.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

$PullDir = Join-Path $RepoRoot "data\news_history_pull"
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$MaxAgeHours = if ($env:NEWS_HYDRATION_MAX_AGE_HOURS) { $env:NEWS_HYDRATION_MAX_AGE_HOURS } else { "48" }
$ReportPath = Join-Path $RepoRoot "logs\news_hydration\last_hydration.json"
$DbPath = if ($env:MACRO_ENGINE_DB_PATH) { $env:MACRO_ENGINE_DB_PATH } else { "data/macro_engine.duckdb" }

Write-Host "Fetching run-history branch..."
& git fetch origin run-history
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path $PullDir) {
    Remove-Item -Recurse -Force $PullDir
}
New-Item -ItemType Directory -Force -Path $PullDir | Out-Null

# A file, not a pipe: Windows PowerShell 5.1 re-encodes a native pipe as text and corrupts the tar stream.
$TarFile = Join-Path $PullDir "run-history-news.tar"
& git archive --format=tar -o $TarFile origin/run-history outputs/news_history
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& tar -x -C $PullDir -f $TarFile
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Remove-Item -Force $TarFile

Write-Host "Hydrating $DbPath from $PullDir\outputs\news_history..."
# Idempotent (insert-if-absent): safe to run daily, and again the same day. Exits non-zero on any hydrate
# error, an empty manifest, or a store whose newest first_seen_at is older than $MaxAgeHours; the JSON
# report is written to $ReportPath either way.
& $Py -m macro_engine.cli import-news-history `
    --db-path $DbPath `
    --snapshot-dir (Join-Path $PullDir "outputs/news_history") `
    --max-age-hours $MaxAgeHours `
    --report-path $ReportPath
exit $LASTEXITCODE
