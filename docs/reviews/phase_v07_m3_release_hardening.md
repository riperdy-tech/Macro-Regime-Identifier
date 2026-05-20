# v0.7-M3 Dashboard Release Hardening Review

Verdict: pass.

Release decision: v0.7 is release-ready as a read-only diagnostic dashboard.

## What v0.7 Adds

v0.7 adds a local dashboard UI under `dashboard/`. The dashboard displays
backend-generated JSON outputs for daily diagnostics, macro context, sector
diagnostics, news scores, combined diagnostics, monitoring, accumulation, and
source coverage.

The backend export command:

```powershell
python -m macro_engine.cli export-dashboard-data
```

copies selected JSON reports into `dashboard/public/data/` and writes a
manifest for the dashboard.

## What v0.7 Does Not Do

v0.7 does not:

- calculate macro, sector, news, or combined scores in the frontend
- call AI providers from the frontend
- store API keys in dashboard files
- modify v0.1 macro formulas
- modify v0.2 sector formulas
- modify v0.3/v0.4/v0.5/v0.6 news, monitoring, or operations logic
- create trading, allocation, execution, or security-selection logic

## Backend Validation Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli export-dashboard-data
```

Results:

```text
pytest: 165 passed, 2 skipped
ruff: passed
validate-config: passed
export-dashboard-data: passed
```

Config validation reported:

```text
13 sources, 11 dimensions, 6 regimes
```

## Full Workflow Refresh

Command run:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
python -m macro_engine.cli export-dashboard-data
```

Result:

```text
run_id: 20260520T122157Z-86752218
run_date: 2026-05-20
status: success
archive_path: outputs\archive\2026-05-20\20260520T122157Z-86752218
warning_count: 0
error_count: 0
```

## Dashboard Data Export Result

Manifest result:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-20
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-16
```

Exported files:

```text
daily_diagnostic_summary.json
current_sector_ranking.json
news_score_report.json
combined_sector_diagnostic.json
news_monitoring_report.json
news_accumulation_report.json
news_source_coverage_report.json
```

## Frontend Build Results

Commands run:

```powershell
cd dashboard
npm install
npm run build
```

Results:

```text
npm install: passed
npm run build: passed
0 npm vulnerabilities
```

## Browser Smoke Check

The dashboard was started locally with:

```powershell
cd dashboard
npm run dev -- --host 127.0.0.1
```

Smoke check URL:

```text
http://127.0.0.1:5173/
```

Pages rendered:

- Overview
- Macro
- Sectors
- News
- Combined
- Monitoring

Browser console error count:

```text
0
```

## Missing Data Behavior

The generated dashboard manifest was temporarily hidden. The dashboard fell back
to committed sample fixtures under `dashboard/public/sample-data/`, rendered
successfully, and produced no browser console errors.

The exported data manifest was restored after the fallback check.

## Current Diagnostic Snapshot

Macro:

```text
reported_regime: reflation
raw_dominant_regime: reflation
macro_date: 2026-05-01
macro_confidence: 13.69%
```

Sector macro top diagnostics:

```text
1. energy
2. materials
3. industrials
```

Top news themes:

```text
1. monetary_tightening
2. commodity_pressure
3. growth_slowdown
```

Combined top diagnostics:

```text
1. energy
2. materials
3. industrials
```

Monitoring:

```text
classification_success_rate: 100%
retry_rate: 0%
repair_rate: 0%
max_rank_change: 1
```

## Guardrail Audit

Scanned:

- dashboard source/static text
- dashboard sample fixtures
- generated Markdown reports under `outputs/`

Forbidden market-action language scan result:

```text
passed
```

The dashboard uses display-only diagnostic language such as confidence,
uncertainty, sector impact, readiness label, diagnostic tailwind, and diagnostic
headwind.

## Repo Hygiene

`git status --short` before staging release-hardening docs showed only intended
documentation changes:

```text
M README.md
M docs/model_limitations.md
?? docs/release_checklist_v0_7.md
```

Confirmed not staged:

- `.env`
- API keys
- `data/`
- `outputs/`
- `outputs/archive/`
- `dashboard/public/data/*.json`
- `dashboard/node_modules/`
- `dashboard/dist/`
- DuckDB files
- caches

## Known Limitations

- The dashboard is a local display layer only.
- Dashboard data can be stale until the backend workflow and
  `export-dashboard-data` are rerun.
- Sample fixtures are synthetic and are not validation evidence.
- The dashboard does not validate predictive usefulness.
- Backend output quality, source coverage, and accumulated-history readiness
  determine what the dashboard can faithfully display.

## Release Blockers

None.

## Non-Blocking Follow-Ups

- Run the daily backend workflow repeatedly and review through the dashboard.
- Add usability improvements after real daily use reveals friction.
- Add historical charts only after there is enough accumulated history to make
  them meaningful.
- Continue improving real-news source coverage outside the dashboard.

## Release Decision

v0.7 is release-ready as a read-only diagnostic dashboard release candidate.

v0.7 is not an investment recommendation system, trading system, allocation
system, execution system, or security-selection system.
