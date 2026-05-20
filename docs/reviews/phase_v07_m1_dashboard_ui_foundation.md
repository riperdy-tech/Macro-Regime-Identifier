# v0.7-M1 Dashboard UI Foundation Review

Verdict: pass.

v0.7-M1 adds a read-only dashboard foundation and a backend export command for
dashboard data. The UI is separate from Python scoring modules and does not
perform scoring or AI classification.

## UI Framework

Framework chosen:

```text
Vite + React + TypeScript
```

Dashboard directory:

```text
dashboard/
```

## Data Export Approach

Added CLI command:

```powershell
python -m macro_engine.cli export-dashboard-data
```

The command copies generated backend JSON files from `outputs/` into:

```text
dashboard/public/data/
```

It writes:

```text
dashboard/public/data/manifest.json
```

Generated dashboard data is ignored by git. The dashboard also includes
synthetic fixtures in `dashboard/public/sample-data/` for development and
missing-data fallback.

## Files Exported

Supported backend JSON files:

```text
daily_diagnostic_summary.json
current_sector_ranking.json
news_score_report.json
combined_sector_diagnostic.json
news_monitoring_report.json
news_accumulation_report.json
news_source_coverage_report.json
```

Latest export result:

```text
data_status: complete
latest_run_date: present in manifest
latest_macro_date: present in manifest
latest_news_score_date: present in manifest
missing_files: none
```

## Manifest Behavior

The manifest includes:

- generated timestamp
- available files
- missing files
- latest run date
- latest macro date
- latest news score date
- data status: `complete`, `partial`, or `missing`

Backend tests cover complete and partial export behavior.

## Pages And Components

The foundation includes:

- app shell
- tab navigation
- reusable metric, panel, table, list, and warning components
- overview view
- macro view
- sector view
- news view
- combined view
- monitoring view
- missing-data fallback

## Missing Data Behavior

If exported data is absent, the app falls back to committed synthetic fixtures.
If both exported data and sample fixtures are unavailable, the app shows a clear
empty state instructing the user to run backend export.

## Verification

Commands run:

```powershell
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm install
npm run build
```

Browser verification:

- local Vite app opened at `http://127.0.0.1:5173/`
- dashboard title rendered
- Overview rendered
- Monitoring tab rendered
- browser console errors: 0

## Known Limitations

- Dashboard data is refreshed manually.
- No charts are included yet; initial views use compact cards and tables.
- The frontend is intentionally read-only.

M2 diagnostic pages can proceed.
