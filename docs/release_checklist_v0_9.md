# v0.9 Release Checklist

Release candidate: `v0.9-rc1`

Status: complete for operating-trial and historical replay release hardening.

## Backend Validation

- [x] `python -m pytest`
- [x] `python -m ruff check .`
- [x] `python -m macro_engine.cli validate-config`
- [x] `python -m macro_engine.cli export-dashboard-data`

## Replay Validation

- [x] `replay-news-history` command exists
- [x] Missing replay CSV blocks cleanly
- [x] 30-day local mapped CSV replay executed in mock mode
- [x] Replay summaries generated
- [x] Replay days archived separately
- [x] Dashboard History can display replay runs
- [x] No future-news leakage detected in replay filtering
- [x] Replay daily runs use isolated temporary databases
- [x] Empty news-sector score days fall back cleanly

## Frontend Validation

- [x] `cd dashboard && npm install`
- [x] `cd dashboard && npm run build`
- [x] History replay display support preserved
- [x] Dashboard export data generated

## Documentation

- [x] README updated with v0.9 replay overview and usage
- [x] README documents replay interpretation limits
- [x] README documents replay data hygiene
- [x] `docs/model_limitations.md` updated with replay limitations
- [x] v0.9-M5 release review created

## Guardrails

- [x] Generated Markdown reports scanned for market-action language
- [x] Dashboard source/static text scanned
- [x] Dashboard sample fixtures scanned
- [x] Touched docs scanned
- [x] No scoring formulas changed
- [x] No frontend scoring logic added
- [x] No frontend AI calls added
- [x] No trading, allocation, execution, or security-selection logic added

## Repo Hygiene

- [x] `.env` not staged
- [x] API keys not staged
- [x] `data/news_pilot/` not staged
- [x] `outputs/` not staged
- [x] `outputs/replay/` not staged
- [x] `outputs/archive/` not staged
- [x] `dashboard/public/data/*.json` not staged
- [x] `dashboard/node_modules/` not staged
- [x] `dashboard/dist/` not staged
- [x] `logs/` not staged
- [x] DuckDB files not staged
- [x] caches not staged
- [x] `.claude/` not staged

## Release Decision

- [x] v0.9 is release-ready as an operating-trial and replay release
- [x] v0.9 replay is not predictive validation
- [x] v0.9 replay is not a trading backtest
- [x] v0.9 does not provide investment advice
- [x] v0.9 does not add trading or allocation logic
