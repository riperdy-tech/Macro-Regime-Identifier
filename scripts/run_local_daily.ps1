# run_local_daily.ps1 - the daily MRI run on the operator's PC (option A, operator 2026-10-04).
#
# Why local: the cost-of-capital anchor needs an equity aggregate built from the screener's data, which only this
# machine has at hand; the cloud run publishes it "degraded" every day (P0.7). This run builds the macro, sector,
# anchor and shock layers here, for rs2-local (which reads outputs/ directly) and for the screener (whose PC run reads
# outputs/ through MRI_OUTPUTS_DIR and commits the public/data/mri/ snapshot the cloud backstop reads). No news: news
# is the cloud job's (live feeds, live AI) - config/daily_pipeline_local.yaml turns it off.
#
# Steps, each logged to logs/local_daily/local_daily_<timestamp>.log; any failure exits non-zero (Task Scheduler's
# "Last Result" shows it):
#   1. the import check (a stale editable install stopped the daily run for four months once)
#   2. refresh data/external/screener_mirror - a shallow, sparse clone of the screener repository's published data
#      (origin/main: public/data/financials/, fundamentals_history.json, cik_map.json, stocks.csv), never the
#      developer tree or the publish clone, which other processes own
#   3. rebuild data/anchors/equity_aggregate.json from the mirror (the input of the implied ERP)
#   4. on the 1st of the month, refresh data/anchors/erp_history.csv (Damodaran, annual; a failure keeps the old file)
#   5. the daily diagnostic with config/daily_pipeline_local.yaml
#   6. refuse success unless outputs/cost_of_capital_anchor.json is dated today (UTC run date: today or yesterday
#      here) and not degraded
param([string]$ScreenerRepoUrl = "https://github.com/riperdy-tech/stock-screener.git")

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Resolve-Path (Join-Path $ScriptDir "..")
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"

$LogDir = Join-Path $RepoRoot "logs\local_daily"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("local_daily_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")

function Log([string]$msg) {
    $line = (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $msg
    Write-Host $line
    Add-Content -Path $LogPath -Value $line -Encoding utf8
}

function Step([string]$name, [scriptblock]$block, [switch]$NonFatal) {
    Log "step: $name"
    # native commands (git, python) write progress to stderr; Windows PowerShell 5.1 turns that into an error record
    # under "Stop", so a step is judged by its exit code alone
    $ErrorActionPreference = "Continue"
    & $block *>> $LogPath
    if ($LASTEXITCODE -ne 0) {
        if ($NonFatal) {
            Log "step FAILED (non-fatal, kept the previous file): $name (exit $LASTEXITCODE)"
            $global:LASTEXITCODE = 0
            return
        }
        Log "step FAILED: $name (exit $LASTEXITCODE); see $LogPath"
        exit $LASTEXITCODE
    }
}

Step "import check" { & $Py (Join-Path $ScriptDir "check_macro_engine_import.py") }

$Mirror = Join-Path $RepoRoot "data\external\screener_mirror"
if (-not (Test-Path (Join-Path $Mirror ".git"))) {
    Step "mirror: clone" { git clone -q --depth 1 --filter=blob:none --no-checkout $ScreenerRepoUrl $Mirror }
    Step "mirror: sparse set" {
        git -C $Mirror sparse-checkout set --no-cone /public/data/financials/ /public/data/fundamentals_history.json /public/data/cik_map.json /public/data/stocks.csv
    }
}
Step "mirror: fetch" { git -C $Mirror fetch -q --depth 1 origin main }
Step "mirror: reset" { git -C $Mirror reset -q --hard FETCH_HEAD }
$MirrorData = Join-Path $Mirror "public\data"
Log ("mirror at " + (git -C $Mirror log -1 --format="%h %cI %s"))

Step "equity aggregate" { & $Py scripts/build_equity_aggregate.py --data-dir $MirrorData --out data/anchors/equity_aggregate.json }

if ((Get-Date).Day -eq 1) {
    Step "erp history (monthly)" { & $Py scripts/fetch_erp_history.py --out data/anchors/erp_history.csv } -NonFatal
}

Step "daily diagnostic (local config)" {
    & $Py -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline_local.yaml --archive
}

Step "anchor check" {
    & $Py -c @"
import json, sys
from datetime import date
a = json.load(open('outputs/cost_of_capital_anchor.json', encoding='utf-8'))
reasons = (a.get('provenance') or {}).get('degradation_reasons') or []
age = (date.today() - date.fromisoformat(a.get('asof') or '1900-01-01')).days   # the run date is UTC: 0 or 1 here
ok = 0 <= age <= 1 and a.get('degraded') is False
print('cost_of_capital_anchor', a.get('asof'), 'degraded', a.get('degraded'), reasons)
sys.exit(0 if ok else 3)
"@
}
Log "done"
exit 0
