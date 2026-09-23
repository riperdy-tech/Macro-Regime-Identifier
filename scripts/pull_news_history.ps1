# N1.4: pull the durable news-history snapshot from the `run-history` branch and
# hydrate it into the LOCAL store. Insert-if-absent, precedence-protected -- the
# 207 real local classifications can never be displaced. Data flows one way,
# cloud -> run-history -> local; this script never pushes.
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot

$PullDir = Join-Path $RepoRoot "data\news_history_pull"
$DbPath = if ($env:MACRO_ENGINE_DB_PATH) { $env:MACRO_ENGINE_DB_PATH } else { "data/macro_engine.duckdb" }

Write-Host "Fetching run-history branch..."
& git fetch origin run-history
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (Test-Path $PullDir) {
    Remove-Item -Recurse -Force $PullDir
}
New-Item -ItemType Directory -Force -Path $PullDir | Out-Null

& git archive origin/run-history outputs/news_history | tar -x -C $PullDir
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Hydrating $DbPath from $PullDir\outputs\news_history..."
& python -m macro_engine.cli import-news-history `
    --db-path $DbPath `
    --snapshot-dir (Join-Path $PullDir "outputs/news_history")
exit $LASTEXITCODE
