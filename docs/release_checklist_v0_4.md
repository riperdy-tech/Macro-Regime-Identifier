# v0.4 Release Checklist

Use this checklist for the real-news monitoring and experimental combined
diagnostic release candidate.

## Repository Hygiene

- [ ] `.env` is not staged.
- [ ] API keys are not written into tracked files.
- [ ] `data/news_pilot/` is not staged unless intentionally using a tiny public example.
- [ ] `outputs/` is not staged.
- [ ] Local DuckDB files are not staged.
- [ ] Cache files are not staged.

## Validation Commands

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml`
- [ ] `python -m macro_engine.cli ingest-news --config config/news_sources.yaml`
- [ ] `python -m macro_engine.cli classify-news --config config/news_ai.yaml`
- [ ] `python -m macro_engine.cli build-news-scores --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli write-news-score-report --config config/news_scoring.yaml`
- [ ] `python -m macro_engine.cli build-combined-sector-diagnostics --config config/sector_news_integration.yaml`
- [ ] `python -m macro_engine.cli write-combined-sector-report --config config/sector_news_integration.yaml`
- [ ] `python -m macro_engine.cli validate-news-monitoring --config config/news_monitoring.yaml`
- [ ] `python -m macro_engine.cli run-news-monitoring --config config/news_monitoring.yaml`
- [ ] `python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml`

## Configuration

- [ ] Production macro config remains `config/phase_b_sources.yaml`.
- [ ] Production sector configs remain unchanged.
- [ ] News classification config remains mock-safe by default.
- [ ] News scoring config remains unchanged unless a bug fix is documented.
- [ ] Monitoring config exists: `config/news_monitoring.yaml`.
- [ ] Balanced pilot profiles are local-data profiles only.
- [ ] Live AI key handling is documented and local-only.

## Monitoring

- [ ] Input quality run records raw and unique item counts.
- [ ] Input quality run records duplicates, date range, source count, and warnings.
- [ ] Classification quality run records success, failure, retry, and repair rates.
- [ ] Overlay monitoring records macro-only versus combined rank changes.
- [ ] Monitoring report flags thin news or source/query concentration.
- [ ] Monitoring report says scoring calibration remains deferred when data is not balanced.

## Reports And Guardrails

- [ ] Macro reports still write.
- [ ] Sector report still writes.
- [ ] News classification report still writes.
- [ ] News score report still writes.
- [ ] Combined diagnostic report still writes.
- [ ] News monitoring report writes.
- [ ] Reports do not imply market action.
- [ ] Reports do not provide allocation, sizing, execution, or security instructions.

## Known Limitations

- [ ] Real-news pilot source bias is documented.
- [ ] RSS/query balance limitations are documented.
- [ ] Old RSS item contamination risk is documented.
- [ ] Classification repair/retry limitations are documented.
- [ ] Monitoring is not empirical validation.
- [ ] Combined overlay remains experimental.

## Release Decision

- [ ] Release blockers are documented.
- [ ] Non-blocking follow-ups are documented.
- [ ] If no blockers, tag `v0.4-rc1`.
