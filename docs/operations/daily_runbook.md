# Daily Operations Runbook

This runbook explains how to run the local daily diagnostic workflow introduced
in v0.5 and prepared for repeated operation in v0.6.

The workflow is diagnostic only. It does not create investment advice, trading
guidance, allocation guidance, execution instructions, or security
instructions.

## Prerequisites

1. Install the project in an activated Python environment.
2. Create a local `.env` from `.env.example`.
3. Set `FRED_API_KEY` for live macro data.
4. Keep `DEEPSEEK_API_KEY` local if live AI classification is used.
5. Leave `config/news_ai.yaml` in mock-safe mode for release checks unless you
   intentionally run live AI.

## Mock Daily Run

Use mock mode for plumbing checks and scheduled dry runs:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
```

## Live AI Daily Run

Live AI must be enabled intentionally:

```powershell
$env:MACRO_ENGINE_LIVE_AI = "1"
$env:MACRO_ENGINE_SOURCE_PROFILE = "daily_local_csv"
.\scripts\run_daily_diagnostic.ps1
```

The script does not print API keys. It writes logs under:

```text
logs/daily/
```

## Local News Inputs

Daily local CSV input is expected at:

```text
data/news_pilot/daily_news_items.csv
```

Required columns:

```text
title,body,source,source_url,published_at
```

Optional columns include:

```text
source_group,query_group,region,sectors_hint,raw_metadata_json
```

Local pilot data stays ignored by git.

## Source Coverage

Validate the source watchlist:

```powershell
python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml
```

Write a coverage report:

```powershell
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
```

The coverage report highlights missing groups, thin groups, stale groups, and
source concentration. It does not change scoring formulas.

## Daily Health Check

Run the health check before scheduling:

```powershell
python -m macro_engine.cli daily-health-check --config config/daily_pipeline.yaml
```

The health check verifies configuration paths, database reachability, archive
writability, source profile availability, live-AI key requirements, and generated
artifact ignore markers.

## Windows Task Scheduler

1. Open Task Scheduler.
2. Create a basic task.
3. Set the trigger to the desired daily time.
4. Choose `Start a program`.
5. Program:

```text
powershell.exe
```

6. Arguments:

```text
-ExecutionPolicy Bypass -File "C:\Users\riper\Documents\New project 3\scripts\run_daily_diagnostic.ps1"
```

7. Start in:

```text
C:\Users\riper\Documents\New project 3
```

Use mock mode by default. Add local environment variables for live AI only on the
machine that owns the API key.

## Cron Example

On Unix-like systems:

```cron
30 7 * * * cd /path/to/project && ./scripts/run_daily_diagnostic.sh
```

## Outputs And Archives

Current reports write under:

```text
outputs/
```

Daily archives write under:

```text
outputs/archive/YYYY-MM-DD/RUN_ID/
```

Daily summaries write to:

```text
outputs/daily_diagnostic_summary.json
outputs/daily_diagnostic_summary.md
```

## Inspecting Status

Latest accumulation status:

```powershell
python -m macro_engine.cli news-accumulation-summary
```

Latest monitoring status:

```powershell
python -m macro_engine.cli news-monitoring-summary
```

Latest combined sector diagnostic:

```powershell
python -m macro_engine.cli current-combined-sector-ranking
```

## Common Failures

Missing `FRED_API_KEY`:
Check `.env` and your shell environment.

Missing local news file:
Use mock mode or place the local CSV under `data/news_pilot/`.

Missing live AI key:
Use mock mode or set `DEEPSEEK_API_KEY` locally before live AI runs.

Guardrail failure:
Inspect the generated Markdown report named in the error. Raw headlines may
need display sanitization while preserving raw storage.

DuckDB lock:
Avoid running multiple CLI commands against the same local database at the same
time.

## Git Hygiene

Do not commit:

```text
.env
data/
data/news_pilot/
outputs/
outputs/archive/
logs/
*.duckdb
```
