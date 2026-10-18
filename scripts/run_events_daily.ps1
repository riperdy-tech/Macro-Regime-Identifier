# run_events_daily.ps1 - the daily candidate-register job (Stage 1, operator decision D3). docs/events_build/STAGE1_RUNBOOK.md.
#
#   1. the import check (a stale editable install must fail loudly, as in run_daily_diagnostic.ps1)
#   2. hydration: scripts/pull_news_history.ps1 (git fetch of run-history, then `import-news-history` with its
#      freshness / exit semantics). A failed hydration stops the job: the register is built only from a hydrated store.
#   3. `events-daily`: freshness check, build, firewall assertion, atomic write of outputs/event_register.json,
#      a dated copy under outputs/archive/, and outputs/event_register_run.json.
#
# Logs: logs/events/events_daily_<timestamp>.log. On any failure the script exits non-zero and writes a short summary to
# logs/events/last_failure.txt (removed on success); the previous event_register.json is left in place.
# No window, no prompt: safe under a hidden scheduled task. It never pushes and never registers anything.
# Registered by a separate, operator-run script (register_events_daily_task.ps1); this script does not.
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs\events"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("events_daily_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$FailPath = Join-Path $LogDir "last_failure.txt"
$DbPath = if ($env:MACRO_ENGINE_DB_PATH) { $env:MACRO_ENGINE_DB_PATH } else { "data/macro_engine.duckdb" }
$MaxAgeHours = if ($env:NEWS_HYDRATION_MAX_AGE_HOURS) { $env:NEWS_HYDRATION_MAX_AGE_HOURS } else { "48" }

function Log([string]$msg) {
    Add-Content -Path $LogPath -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $msg) -Encoding utf8
}

function Fail([string]$step, [int]$code) {
    $text = "events_daily FAILED at step '$step' (exit $code) on " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "`r`n" +
            "log: $LogPath`r`n" +
            "The previous outputs\event_register.json was left in place; outputs\event_register_run.json says what failed."
    Set-Content -Path $FailPath -Value $text -Encoding utf8
    Log "FAILED at $step (exit $code)"
    exit $(if ($code -ne 0) { $code } else { 1 })
}

Log "events_daily start; repo $RepoRoot"

& $Py (Join-Path $ScriptDir "check_macro_engine_import.py") *>> $LogPath
if ($LASTEXITCODE -ne 0) { Fail "import_check" $LASTEXITCODE }

Log "step: hydration"
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ScriptDir "pull_news_history.ps1") *>> $LogPath
if ($LASTEXITCODE -ne 0) { Fail "hydration" $LASTEXITCODE }

Log "step: events-daily"
& $Py -m macro_engine.cli events-daily --db-path $DbPath --outputs-dir outputs --max-age-hours $MaxAgeHours *>> $LogPath
if ($LASTEXITCODE -ne 0) { Fail "events-daily" $LASTEXITCODE }

Remove-Item -Force -ErrorAction SilentlyContinue $FailPath
Log "events_daily ok"
exit 0
