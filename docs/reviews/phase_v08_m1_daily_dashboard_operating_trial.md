# v0.8-M1 Daily Dashboard Operating Trial Review

Verdict: pass.

v0.8-M1 keeps the dashboard as a read-only display layer and improves the daily
operating loop around it. No scoring formulas, AI behavior, or backend model
logic were changed.

## Operating Flow Tested

Daily local review flow:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm run dev
```

Dashboard pages reviewed:

- Overview
- Macro
- Sectors
- News
- Combined
- Monitoring
- History

## Operating Aids Added

Created:

```text
docs/operations/daily_dashboard_checklist.md
docs/operations/dashboard_issue_log_template.md
```

The checklist covers daily backend execution, dashboard export, dashboard
startup, run-date checks, macro-date checks, readiness label checks, guardrail
status, source coverage warnings, archive path, and issue capture.

The issue template records date, run id, page, issue type, observed behavior,
expected behavior, data file, severity, and follow-up.

## Dashboard Status Improvements

The Overview page now highlights:

- latest run id
- archive path
- exported timestamp
- manifest data status
- missing file count or complete file set
- top sector diagnostics
- top news themes
- combined top diagnostics

The Monitoring page now includes a plain-language readiness explanation so the
label is easier to interpret during daily use.

## Latest Data Status

Latest exported dashboard manifest:

```text
data_status: complete
latest_run_date: 2026-05-20
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-16
```

## Usability Issues Found

The dashboard previously had no single place for recent archived runs. That made
daily comparison awkward after multiple runs.

## Fixes Made

- Added daily dashboard checklist.
- Added issue log template.
- Added clearer Overview status cards.
- Added readiness explanation in Monitoring.
- Proceeded to M2 for lightweight history visibility.

## Issues Deferred

- More detailed charts should wait for more accumulated real history.
- Any formula or weighting changes remain out of scope.
- Real source-balance improvements remain a backend operating task.

## Tests And Build

Validation commands:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
cd dashboard
npm run build
```

Result:

```text
passed
```

## M2 Readiness

M2 history views can proceed because the dashboard export path and archive
structure are available.
