# run_health_watch.ps1 - the MRI health watch (WO_MA1): checks what MRI-Local-Daily and MRI-Events-Daily left behind and
# sends the result to GitHub as a heartbeat, so a failure reaches the operator (a failed Actions run is emailed) and a
# silent PC is caught by the dead-man check in .github/workflows/pc-watch.yml.
#
#   1. fetch the website's regime file (a failed fetch is not an error: the comparison is then reported as unavailable)
#   2. `health-watch` -> outputs\health_watch.json (it always exits 0; the JSON is the result)
#   3. `gh workflow run pc-watch.yml` with status, changed and one line per finding. Every run sends it. If gh is
#      missing or the dispatch fails, this script logs it and exits non-zero (Task Scheduler's "Last Result").
#
# Logs: logs\health_watch\health_watch_<timestamp>.log. Never pushes, never writes outside this repository's outputs\
# and logs\, reads the store read-only. Registered by a separate operator-run script (register_health_watch_task.ps1).
# The alert text carries job names, input ids, dates and counts only: the repository is public.
param([string]$ExportDir = "C:\ProgramData\stocks-mri\outputs",
      [string]$Repo = "riperdy-tech/Macro-Regime-Identifier",
      [string]$SiteRegimeUrl = "https://riperdy-tech.github.io/Macro-Regime-Identifier/data/regime_status.json")
$ErrorActionPreference = "Continue"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $RepoRoot
$Py = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs\health_watch"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogPath = Join-Path $LogDir ("health_watch_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".log")
$Summary = Join-Path $RepoRoot "outputs\health_watch.json"
$SiteCopy = Join-Path ([System.IO.Path]::GetTempPath()) "mri_site_regime_status.json"

function Log([string]$msg) {
    Add-Content -Path $LogPath -Value ((Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " " + $msg) -Encoding utf8
}

# 1. the website's copy of the regime (regime_status.json is what the dashboard publishes under /data/)
Remove-Item $SiteCopy -ErrorAction SilentlyContinue
try {
    Invoke-WebRequest -Uri $SiteRegimeUrl -OutFile $SiteCopy -UseBasicParsing -TimeoutSec 60
    Log "fetched the website regime file"
} catch {
    Log "website regime file not fetched (comparison will be unavailable): $_"
    Remove-Item $SiteCopy -ErrorAction SilentlyContinue
}

# 2. the watch
Remove-Item $Summary -ErrorAction SilentlyContinue
$WatchArgs = @("-m", "macro_engine.cli", "health-watch", "--outputs-dir", "outputs", "--export-dir", $ExportDir,
               "--db-path", "data/macro_engine.duckdb")
if (Test-Path $SiteCopy) { $WatchArgs += @("--compare-regime", $SiteCopy) }
& $Py @WatchArgs *>> $LogPath
Log "health-watch exit $LASTEXITCODE"

$Status = "critical"; $Changed = "true"
$Message = "health-watch did not produce a result (see the PC log)"
if (Test-Path $Summary) {
    try {
        $doc = Get-Content $Summary -Raw -Encoding utf8 | ConvertFrom-Json
        $Status = [string]$doc.status
        $Changed = if ($doc.changed) { "true" } else { "false" }
        $lines = @($doc.findings | ForEach-Object { "[" + $_.severity + "] " + $_.code + " " + $_.subject + ": " + $_.detail })
        $Message = if ($lines.Count -gt 0) { $lines -join "`n" } else { "no findings" }
    } catch {
        Log "health_watch.json not readable: $_"
    }
}
# the dispatch carries the text as one argument: no double quotes or backticks to be mangled by the command line
$Message = ($Message -replace '"', "'" -replace '`', "'")
if ($Message.Length -gt 1500) { $Message = $Message.Substring(0, 1500) }
Log "status $Status changed $Changed"

# 3. the heartbeat
$Gh = Get-Command gh -ErrorAction SilentlyContinue
if (-not $Gh) { Log "gh is not installed or not on PATH: heartbeat NOT sent"; exit 2 }
& $Gh.Source workflow run pc-watch.yml -R $Repo -f "status=$Status" -f "changed=$Changed" -f "message=$Message" *>> $LogPath
if ($LASTEXITCODE -ne 0) { Log "gh workflow run FAILED (exit $LASTEXITCODE): heartbeat NOT sent"; exit 3 }
Log "heartbeat sent"
exit 0
