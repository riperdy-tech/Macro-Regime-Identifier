# v0.6 Release Checklist

v0.6 is a real-news operations and source-coverage release. It improves source
group mapping, bounded live operation, scheduled-run support, and readiness
review hygiene. It is not a validation, allocation, or trading release.

## Validation

- [ ] `python -m pytest`
- [ ] `python -m ruff check .`
- [ ] `python -m macro_engine.cli validate-config`
- [ ] `python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml`
- [ ] `python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml`
- [ ] `python -m macro_engine.cli daily-health-check --config config/daily_pipeline.yaml`

## Daily Operation

- [ ] Mock daily run succeeds:
  `python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive`
- [ ] Bounded live run is documented if local live AI config and real news data are available.
- [ ] Live classification is capped by `live_ai_safety.max_items_per_run`.
- [ ] Live classification uses `only_unclassified` by default.
- [ ] Interrupted live runs can resume without reprocessing completed classifications.
- [ ] Archive path is created for daily runs.

## Source Coverage

- [ ] `config/news_sources.yaml` includes source group mapping rules.
- [ ] `config/news_source_watchlist.yaml` validates.
- [ ] Source coverage report includes source group count.
- [ ] Source coverage report includes unmapped item count and percentage.
- [ ] Source coverage report includes old item count and percentage.
- [ ] Missing or stale source groups are documented.
- [ ] Balanced local pilot data, if used, remains local-only.

## Accumulation And Monitoring

- [ ] `python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml`
- [ ] `python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml`
- [ ] `python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml`
- [ ] Readiness label is honest.
- [ ] No validation claim is made without enough balanced real-news history.

## Guardrails

- [ ] Generated Markdown reports pass forbidden market-action language scan.
- [ ] Reports describe diagnostics, uncertainty, source coverage, and readiness only.
- [ ] No trading, allocation, position sizing, execution, or security-selection logic is introduced.

## Repository Hygiene

- [ ] `.env` is not staged.
- [ ] API keys are not staged.
- [ ] `data/` is not staged except intentional examples.
- [ ] `data/news_pilot/` is not staged.
- [ ] `outputs/` and `outputs/archive/` are not staged.
- [ ] `logs/` is not staged.
- [ ] DuckDB files are not staged.
- [ ] Caches are not staged.

## Release Decision

- [ ] v0.6 is release-ready as a real-news operations and source-coverage release.
- [ ] v0.6 is not release-ready.
- [ ] v0.6 is blocked pending more balanced real-news data.
