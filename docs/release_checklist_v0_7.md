# v0.7 Release Checklist

Release candidate: `v0.7-rc1`

Status: complete for read-only dashboard release hardening.

## Backend Validation

- [x] `python -m pytest`
- [x] `python -m ruff check .`
- [x] `python -m macro_engine.cli validate-config`
- [x] `python -m macro_engine.cli export-dashboard-data`
- [x] Mock daily diagnostic run completed and refreshed dashboard data

## Frontend Validation

- [x] `cd dashboard && npm install`
- [x] `cd dashboard && npm run build`
- [x] Local dashboard started with `npm run dev`
- [x] Overview page rendered
- [x] Macro panel rendered
- [x] Sector panel rendered
- [x] News panel rendered
- [x] Combined panel rendered
- [x] Monitoring panel rendered
- [x] Missing exported data fell back to sample fixtures without crashing
- [x] Browser console error check passed

## Guardrails

- [x] Dashboard source/static text scanned for market-action language
- [x] Generated Markdown reports scanned for market-action language
- [x] Dashboard remains display-only
- [x] No frontend scoring logic added
- [x] No frontend AI calls added
- [x] No API keys added to dashboard files

## Repo Hygiene

- [x] `.env` not staged
- [x] API keys not staged
- [x] `data/` not staged except intentional examples already tracked
- [x] `outputs/` not staged
- [x] `outputs/archive/` not staged
- [x] `dashboard/public/data/*.json` not staged
- [x] `dashboard/node_modules/` not staged
- [x] `dashboard/dist/` not staged
- [x] DuckDB files not staged
- [x] caches not staged

## Release Decision

- [x] v0.7 is release-ready as a read-only diagnostic dashboard release
- [x] v0.7 is not a scoring, validation, trading, allocation, or security-selection release
