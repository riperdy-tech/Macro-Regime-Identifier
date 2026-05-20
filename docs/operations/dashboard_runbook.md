# Dashboard Runbook

The v0.7 dashboard is a local read-only viewer for generated backend outputs.
It does not calculate scores and it does not call AI providers.

## Prerequisites

- Python environment for the backend.
- Node.js and npm for the dashboard.
- Backend outputs generated under `outputs/`.

## Refresh Dashboard Data

Run from the repository root:

```powershell
python -m macro_engine.cli export-dashboard-data
```

This copies selected JSON files from `outputs/` to:

```text
dashboard/public/data/
```

It also writes:

```text
dashboard/public/data/manifest.json
```

The manifest records available files, missing files, latest run date, latest
macro date, latest news score date, and data status.

## Start The Dashboard

```powershell
cd dashboard
npm install
npm run dev
```

Open the local Vite URL printed by npm, usually:

```text
http://127.0.0.1:5173/
```

## Build The Dashboard

```powershell
cd dashboard
npm run build
```

## Typical Daily Review Flow

```powershell
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
python -m macro_engine.cli export-dashboard-data
cd dashboard
npm run dev
```

Use a local live AI config only when intentionally running live classification.
Never place API keys in dashboard files.

## Data Sources

The dashboard reads exported files from:

```text
dashboard/public/data/
```

If exported files are not available, it falls back to synthetic fixtures:

```text
dashboard/public/sample-data/
```

Sample fixtures are safe for development. Real generated dashboard data is
ignored by git.

## Troubleshooting

If the dashboard says data is unavailable:

1. Run the backend daily workflow.
2. Run `python -m macro_engine.cli export-dashboard-data`.
3. Refresh the browser.

If only partial data appears:

1. Check `dashboard/public/data/manifest.json`.
2. Review the `missing_files` list.
3. Re-run the backend report command that creates the missing output.

If the dashboard build fails:

1. Run `npm install` in `dashboard/`.
2. Run `npm run build` again.
3. Check TypeScript output for the failing file.

## Git Hygiene

Do not stage:

- `.env`
- API keys
- `data/`
- `outputs/`
- `dashboard/public/data/*.json`
- `dashboard/node_modules/`
- `dashboard/dist/`

The dashboard is for local diagnostic review. It does not validate model
performance or convert diagnostics into actions.
