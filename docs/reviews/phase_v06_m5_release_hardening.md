# v0.6-M5 Release Hardening Review

Verdict: pass.

Release decision: v0.6 is release-ready as a real-news operations and
source-coverage release candidate.

v0.6 does not validate predictive performance. It improves the operating
workflow needed to collect, map, monitor, and accumulate real-news history.

## What v0.6 Adds

- source watchlist and source coverage reporting
- local CSV/JSON and RSS source profiles
- source group mapping rules for real-news pilot files
- bounded live classification for daily operation
- `only_unclassified` resume behavior
- scheduled-run scripts and daily runbook
- daily health check
- source coverage thresholds for unmapped, old, stale, missing, and concentrated data
- accumulated-history readiness review

## What v0.6 Does Not Do

v0.6 does not:

- change v0.1 macro formulas
- change v0.2 sector assumptions
- change v0.3/v0.4 news or combined scoring formulas
- validate predictive performance
- create trading rules
- create allocation rules
- create execution logic
- create security selections

## Validation Results

Commands run:

```powershell
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
python -m macro_engine.cli daily-health-check --config config/daily_pipeline.yaml
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive --db-path data/news_pilot/v06_m5_mock.duckdb
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
```

Results:

```text
pytest: 162 passed, 2 skipped
ruff: passed
validate-config: passed
validate-news-sources: passed
source coverage report: generated
daily health check: valid, warning status
mock daily run: success
accumulation report: generated
monitoring report: generated
```

The daily health check warning is expected for the default synthetic sample
profile because it has six short synthetic items and no source group mapping.
That is not a release blocker.

## Source Coverage Summary

Latest source coverage report:

```text
stored_item_count: 126
source_group_count: 7
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 16
old_item_pct: 12.7%
```

Configured source groups with stored data:

```text
credit_financial_conditions
energy_commodities
geopolitical
inflation_rates
labor
macro_general
real_estate
```

Configured source groups still missing stored data:

```text
consumer
defensive_sectors
healthcare
manufacturing_industrials
technology_ai
```

Latest-date source coverage is concentrated in `inflation_rates`, and several
groups are stale. This is a non-blocking limitation and the main follow-up for
continued daily operation.

## Source Group Mapping Status

The expanded real-news pilot previously had 120 unmapped RSS-derived items.
After M4/M5 mapping:

```text
unmapped real-news items: 0
source groups represented: 7
```

The mapping is auditable:

- explicit `source_group` fields are preferred
- `query_group` may supply a group when explicit
- configured `source_group_rules` apply only when a rule matches
- unmapped remains visible when no rule applies

## Latest Bounded Live Run

The latest bounded live run used local mapped real-news pilot data:

```text
run_id: 20260520T001904Z-1f27ea6b
run_date: 2026-05-20
status: success
selected live classification items: 25
successful live classifications: 25
failed live classifications: 0
archive_path: outputs\archive\2026-05-20\20260520T001904Z-1f27ea6b
warning_count: 0
error_count: 0
```

The run emitted per-item progress and completed without silent hanging.

## Mock Daily Release Run

The release-mode mock daily run completed on an ignored local DB:

```text
run_id: 20260520T003028Z-91aa825f
run_date: 2026-05-20
status: success
classified sample items: 6
archive_path: outputs\archive\2026-05-20\20260520T003028Z-91aa825f
warning_count: 0
error_count: 0
```

## Accumulation Readiness

Latest accumulation summary:

```text
raw_item_count: 126
new_unique_items: 126
classified_items: 75
failed_items: 0
success_rate: 100.0%
source_count: 73
source_group_count: 7
readiness_label: insufficient_history
```

The readiness label is honest. The system has operational history, but not
enough repeated balanced real-news run dates for validation.

## Current Macro Regime

Latest valid macro date:

```text
2026-05-01
```

Macro output:

```text
reported_regime: reflation
raw_dominant_regime: reflation
raw_dominant_probability: 38.68%
macro_confidence: 13.69%
```

## Current Sector Ranking

Top macro sector diagnostics:

```text
1. energy
2. materials
3. industrials
4. financials
5. consumer_staples
```

## Current News Themes

Latest news score date:

```text
2026-05-16
```

Top news themes:

```text
inflation_pressure
monetary_tightening
energy_supply_shock
labor_strength
monetary_easing
```

## Current Combined Sector Ranking

Latest combined diagnostic ranking:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

The news overlay remains bounded:

```text
max_rank_change: 1
```

## Guardrail Audit

Generated Markdown reports were scanned for forbidden market-action language.
No matches were found.

## Repo Hygiene

Repo hygiene was checked with `git status --short`.

Expected local-only generated artifacts remain ignored:

- `data/news_pilot/news_items_balanced.csv`
- `data/news_pilot/v06_m5_mock.duckdb`
- `outputs/`
- `outputs/archive/`
- `logs/`

No API keys, `.env`, local pilot data, outputs, logs, DuckDB files, or caches
should be staged.

## Release Blockers

None.

## Non-Blocking Follow-Ups

- Continue repeated bounded real-news daily runs.
- Improve coverage for missing groups:
  `consumer`, `defensive_sectors`, `healthcare`, `manufacturing_industrials`,
  and `technology_ai`.
- Reduce latest-date concentration in `inflation_rates`.
- Remove or replace stale/old RSS items in local pilot inputs.
- Review source group mapping rules periodically.
- Re-run readiness review only after enough real run dates accumulate.

## Final Decision

v0.6 is release-ready as a real-news operations and source-coverage release
candidate.

This is not investment advice. This is not a trading system. This is not an
allocation system. This release does not validate predictive performance.
