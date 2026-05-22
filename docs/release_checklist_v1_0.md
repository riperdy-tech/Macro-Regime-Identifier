# v1.0 Release Checklist

Release target: `v1.0-rc1`

Release position: local-first macro, sector, news, operations, replay, and
read-only dashboard diagnostic platform.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive`
- [ ] `python -m macro_engine.cli export-dashboard-data`
- [ ] `cd dashboard && npm install`
- [ ] `cd dashboard && npm run build`
- [ ] Local dashboard smoke check, if practical
- [ ] GitHub Pages availability check, if applicable

## Expected Outputs

- [ ] Daily diagnostic summary generated
- [ ] Daily archive path created
- [ ] Dashboard data manifest generated
- [ ] Dashboard History data generated
- [ ] Dashboard build artifact generated locally

## Guardrails

- [ ] Docs and dashboard text audited for market-action wording
- [ ] Generated Markdown reports audited
- [ ] Any market-action wording appears only in limitation or disclaimer context
- [ ] Dashboard remains display-only
- [ ] No frontend AI calls
- [ ] No frontend scoring logic

## Repo Hygiene

- [ ] `.env` not staged
- [ ] API keys not staged
- [ ] `data/news_pilot/` not staged
- [ ] `outputs/` not staged
- [ ] `outputs/archive/` not staged
- [ ] `outputs/replay/` not staged
- [ ] `dashboard/public/data/` generated files not staged
- [ ] `dashboard/node_modules/` not staged
- [ ] `dashboard/dist/` not staged unless intentionally deployed
- [ ] `logs/` not staged
- [ ] DuckDB files not staged
- [ ] caches not staged
- [ ] `.claude/` not staged

## Release Decision

- [ ] Release-ready as `v1.0-rc1`
- [ ] Not release-ready
- [ ] Blocked pending issue

## Required Positioning

`v1.0-rc1` is diagnostic software. It is not investment advice, not a trading
system, not an allocation system, and not a performance-validated forecasting
model. Historical operating replay is not predictive validation, and macro data
is not vintage unless separately supported.
