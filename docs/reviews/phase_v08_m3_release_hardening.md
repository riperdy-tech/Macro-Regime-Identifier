# v0.8-M3 Dashboard Operating Trial Release Hardening Review

Verdict: pass.

Release decision: v0.8 is release-ready as a daily dashboard operating trial.

## What v0.8 Adds

v0.8 improves the daily dashboard operating loop:

- daily dashboard checklist
- dashboard issue log template
- clearer Overview data freshness/status cards
- readiness label explanation
- lightweight `history_index.json` export
- History tab for recent archived daily summaries
- GitHub Pages dashboard deployment support

## What v0.8 Does Not Do

v0.8 does not:

- calculate new scores in the frontend
- call AI providers from the frontend
- add API keys to frontend files
- change macro formulas
- change sector assumptions
- change news scoring formulas
- change combined diagnostic formulas
- validate predictive performance
- add trading, allocation, execution, or security-selection logic

## Validation Command Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
```

Results:

```text
pytest: 166 passed, 2 skipped
ruff: passed
validate-config: passed
```

Config validation reported:

```text
13 sources, 11 dimensions, 6 regimes
```

## Daily Diagnostic Run Result

Command:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
```

Result:

```text
run_id: 20260520T173009Z-2d49576f
run_date: 2026-05-20
status: success
archive_path: outputs\archive\2026-05-20\20260520T173009Z-2d49576f
warning_count: 0
error_count: 0
```

## Dashboard Export Result

Command:

```powershell
python -m macro_engine.cli export-dashboard-data
```

Result:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-20
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-16
```

Exported files include:

```text
daily_diagnostic_summary.json
current_sector_ranking.json
news_score_report.json
combined_sector_diagnostic.json
news_monitoring_report.json
news_accumulation_report.json
news_source_coverage_report.json
history_index.json
```

## History Index Result

`history_index.json` result:

```text
history_status: available
recorded_run_count: 9
latest_run: 20260520T173009Z-2d49576f
latest_run_date: 2026-05-20
```

The history view remains an operating summary. It is not a backtest or
performance validation layer.

## Frontend Build Results

Commands run:

```powershell
cd dashboard
npm install
npm run build
$env:GITHUB_PAGES='true'; npm run build; Remove-Item Env:GITHUB_PAGES
```

Results:

```text
npm install: passed
local build: passed
GitHub Pages build: passed
```

## Browser Smoke Check

Local dashboard URL:

```text
http://127.0.0.1:5173/
```

Visible sections:

- Overview
- Macro
- Sectors
- News
- Combined
- Monitoring
- History

Browser console error count:

```text
0
```

GitHub Pages URL check:

```text
https://riperdy-tech.github.io/Macro-Regime-Identifier/
status: 200
title: Macro Diagnostic Dashboard
```

## Missing Data And Short-History Behavior

The dashboard retains the sample-data fallback path from v0.7/v0.8-M2. The
History tab displays a short-history message when there are fewer than five
history rows and displays an empty-state message when no archived daily runs are
available.

Current local history has enough archived summaries to show recent-run rows, but
the feature is still operating context only.

## Guardrail Audit

Scanned:

- dashboard source/static text
- dashboard sample fixtures
- generated Markdown reports under `outputs/`

Forbidden market-action language scan result:

```text
passed
```

## Repo Hygiene

`git status --short` before staging showed:

```text
M README.md
M docs/model_limitations.md
?? docs/release_checklist_v0_8.md
?? docs/reviews/phase_v08_m3_release_hardening.md
?? .claude/
```

`.claude/` is local-only and was not staged.

Confirmed not staged:

- `.env`
- API keys
- `data/`
- `outputs/`
- `outputs/archive/`
- `dashboard/public/data/*.json`
- `dashboard/node_modules/`
- `dashboard/dist/`
- `logs/`
- DuckDB files
- caches

## Known Limitations

- v0.8 history rows depend on archived daily summaries.
- Archived summaries may have different fields across versions.
- Short history does not support validation claims.
- The dashboard remains display-only.
- Backend output quality determines dashboard quality.
- GitHub Pages displays committed sample/exported static data only; local
  generated dashboard data remains ignored by git.

## Release Blockers

None.

## Non-Blocking Follow-Ups

- Operate the dashboard daily and record real usability issues.
- Expand real source coverage before any validation effort.
- Add richer history charts only after enough real operating history exists.
- Continue keeping frontend logic display-only.

## Release Decision

v0.8 is release-ready as a daily dashboard operating trial.

v0.8 does not validate performance, does not provide investment advice, does not
add trading logic, and does not add allocation logic. History views are archived
operating summaries only.
