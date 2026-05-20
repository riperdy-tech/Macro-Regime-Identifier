# v0.7-M2 Read-Only Diagnostic Pages Review

Verdict: pass.

v0.7-M2 makes the dashboard useful for daily review by rendering macro, sector,
news, combined, monitoring, accumulation, and source coverage outputs from
generated JSON snapshots.

## Pages Completed

Implemented dashboard tabs:

- Overview
- Macro
- Sectors
- News
- Combined
- Monitoring

Overview displays:

- run status
- run id
- macro regime
- macro confidence
- macro date
- top sector diagnostics
- top news themes
- combined top sectors
- coverage warnings

Macro displays:

- reported regime
- raw leader
- confidence
- macro date
- regime probabilities when present
- warnings

Sectors displays:

- sector ranking
- confidence-adjusted scores
- top and lowest sector component excerpts when available

News displays:

- news score date
- classification success
- retry and repair rates
- macro themes
- sector diagnostic tailwinds and headwinds
- low-confidence items

Combined displays:

- combined ranking
- macro-only top sectors from monitoring when available
- rank changes from news overlay
- news item count
- overlay status

Monitoring displays:

- readiness label
- source group count
- unmapped share
- old item share
- input quality
- guardrail status
- missing source groups
- source group counts

## Sample Fixtures

Synthetic dashboard fixtures were added under:

```text
dashboard/public/sample-data/
```

They are safe to commit and provide fallback data for development. Real exported
dashboard data remains under `dashboard/public/data/` and is ignored by git.

## Dashboard Data Refresh

Command:

```powershell
python -m macro_engine.cli export-dashboard-data
```

Latest manifest generated successfully with complete data.

## Build And Test Results

Frontend:

```text
npm install: passed
npm run build: passed
```

Backend export tests were added for:

- copying available files
- manifest missing-file behavior
- CLI export path

## Guardrail Audit

Dashboard source text avoids market-action language and uses diagnostic labels:

- diagnostic tailwind
- diagnostic headwind
- experimental overlay
- confidence
- uncertainty
- readiness label

The dashboard does not contain frontend scoring logic, frontend AI calls, or API
key handling.

## Known Limitations

- The dashboard is local only.
- Data refresh is manual.
- The first version uses tables and compact panels rather than charts.
- Displayed data can be stale if `export-dashboard-data` has not been run.
- The UI does not validate model performance.

## Next Step

v0.7-M3 release hardening can proceed after final full validation.
