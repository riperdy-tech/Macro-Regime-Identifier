# v0.5 Release Checklist

Use this checklist for the daily operations and news accumulation release
candidate.

## Repository Hygiene

- [ ] `.env` is not staged.
- [ ] API keys are not written into tracked files.
- [ ] `data/` is not staged except intentional example files.
- [ ] `data/news_pilot/` is not staged.
- [ ] `outputs/` is not staged.
- [ ] `outputs/archive/` is not staged.
- [ ] Local DuckDB files are not staged.
- [ ] Cache files are not staged.

## Validation Commands

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive`
- [ ] `python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml`
- [ ] `python -m macro_engine.cli news-accumulation-summary`
- [ ] `python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml`
- [ ] `python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml`
- [ ] `python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml`
- [ ] `python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml`

## Daily Operations

- [ ] `config/daily_pipeline.yaml` is present.
- [ ] Daily diagnostic run records step statuses.
- [ ] Daily diagnostic run records warnings and errors.
- [ ] Daily summary JSON writes.
- [ ] Daily summary Markdown writes.
- [ ] Archive path is generated when archive mode is enabled.
- [ ] Guardrail audit result is recorded.
- [ ] Mock mode works without a live AI key.

## News Accumulation

- [ ] `config/news_accumulation.yaml` is present.
- [ ] Accumulation run records raw item count.
- [ ] Accumulation run records classified item count.
- [ ] Accumulation run records source and source group counts.
- [ ] Accumulation report writes.
- [ ] Readiness label is present.
- [ ] Readiness label does not imply empirical validation.

## Reports And Guardrails

- [ ] Macro pipeline reports still write.
- [ ] Sector report still writes.
- [ ] News score report still writes.
- [ ] Combined diagnostic report still writes.
- [ ] News monitoring report still writes.
- [ ] Daily diagnostic summary writes.
- [ ] News accumulation report writes.
- [ ] Generated Markdown reports do not imply market action.
- [ ] Reports do not provide allocation, sizing, execution, or security instructions.

## Known Limitations

- [ ] Mock runs do not validate signal quality.
- [ ] Real validation requires repeated balanced real-news runs.
- [ ] Accumulation history can be insufficient.
- [ ] Source coverage and source group coverage limitations are documented.
- [ ] Local archives are generated artifacts.
- [ ] v0.5 does not validate predictive performance.

## Release Decision

- [ ] Release blockers are documented.
- [ ] Non-blocking follow-ups are documented.
- [ ] If no blockers, tag `v0.5-rc1`.
