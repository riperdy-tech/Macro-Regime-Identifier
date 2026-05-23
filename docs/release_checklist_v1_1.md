# v1.1 Release Checklist

Release target: `v1.1-rc1`

Release position: v1.1 is an operations release. It adds real daily operations
trials, source coverage improvement, source freshness review, multi-day
operating evidence, and daily wrapper observability improvements. It does not
change scoring formulas or add trading/allocation logic.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --archive`
- [ ] `python -m macro_engine.cli export-dashboard-data`
- [ ] `python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml`
- [ ] `python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml`
- [ ] `python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml`
- [ ] `cd dashboard && npm run build`
- [ ] Dashboard smoke check, if practical
- [ ] GitHub Pages availability check, if applicable

## Expected Outputs

- [ ] Daily diagnostic summary generated
- [ ] Daily archive path created
- [ ] Dashboard data manifest generated
- [ ] Dashboard History data generated
- [ ] Accumulation report generated
- [ ] Monitoring report generated
- [ ] Source coverage report generated
- [ ] Dashboard build artifact generated locally

## Documentation

- [ ] README.md updated with v1.1 positioning
- [ ] docs/model_limitations.md updated with v1.1 operational limitations
- [ ] docs/release_checklist_v1_1.md created
- [ ] docs/reviews/phase_v11_m5_release_hardening.md created

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

- [ ] Release-ready as `v1.1-rc1`
- [ ] Not release-ready
- [ ] Blocked pending issue

## Required Positioning

- [ ] v1.1 is not investment advice
- [ ] v1.1 is not a trading system
- [ ] v1.1 is not an allocation system
- [ ] v1.1 does not validate predictive performance
- [ ] v1.1 does not add scoring formula changes
- [ ] v1.1 does not add trading/allocation/recommendation logic
