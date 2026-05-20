# v0.8 Release Checklist

Release candidate: `v0.8-rc1`

Status: complete for dashboard operating trial release hardening.

## Backend Validation

- [x] `python -m pytest`
- [x] `python -m ruff check .`
- [x] `python -m macro_engine.cli validate-config`
- [x] Daily diagnostic run completed
- [x] Archive created
- [x] Dashboard data export completed
- [x] `history_index.json` generated

## Frontend Validation

- [x] `cd dashboard && npm install`
- [x] Local dashboard build passed
- [x] GitHub Pages dashboard build passed
- [x] Browser smoke check passed
- [x] Overview visible
- [x] Macro visible
- [x] Sectors visible
- [x] News visible
- [x] Combined visible
- [x] Monitoring visible
- [x] History visible
- [x] Missing/sample fallback behavior previously verified and preserved

## Guardrails

- [x] Dashboard source/static text scanned for market-action language
- [x] Dashboard sample fixtures scanned
- [x] Generated Markdown reports scanned
- [x] No frontend scoring logic added
- [x] No frontend AI calls added
- [x] No API keys added to frontend
- [x] No trading, allocation, execution, or security-selection logic added

## Repo Hygiene

- [x] `.env` not staged
- [x] API keys not staged
- [x] `data/` not staged except intentional examples already tracked
- [x] `outputs/` not staged
- [x] `outputs/archive/` not staged
- [x] `dashboard/public/data/*.json` not staged
- [x] `dashboard/node_modules/` not staged
- [x] `dashboard/dist/` not staged
- [x] `logs/` not staged
- [x] DuckDB files not staged
- [x] caches not staged

## Release Decision

- [x] v0.8 is release-ready as a daily dashboard operating trial
- [x] v0.8 does not validate performance
- [x] v0.8 does not provide investment advice
- [x] v0.8 does not add trading or allocation logic
