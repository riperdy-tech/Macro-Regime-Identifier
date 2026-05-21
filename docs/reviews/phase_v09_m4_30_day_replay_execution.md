# v0.9-M4 30-Day Mapped News Replay Execution

## Summary

v0.9-M4 passes as replay execution infrastructure and a bounded mock-mode historical news replay.

The missing 30-day mapped CSV was created locally at:

```text
data/news_pilot/news_items_last_30_days.csv
```

The CSV is local-only and remains unstaged. It was built from public RSS-derived news search results, mapped into the required source groups, and used to run the existing `replay-news-history` workflow across a 30-day replay window.

This milestone also surfaced and fixed two operational replay bugs:

1. Replay classification initially leaked into the shared local database and selected unrelated unclassified rows.
2. Combined diagnostics failed on days where the mock classifier produced macro theme scores but no sector-impact rows.

Both issues were fixed before the final replay:

- replay daily runs now use isolated temporary DuckDB files, so each replay date is bounded to its selected input slice;
- combined diagnostics now handle empty news-sector score tables by falling back to macro-only behavior.

## Data File

Status: created locally

Tracked in git: no

Path:

```text
data/news_pilot/news_items_last_30_days.csv
```

Rows: 144

Required columns present:

- `title`
- `body`
- `source`
- `source_url`
- `published_at`
- `source_group`

Optional columns included:

- `query_group`
- `region`
- `sectors_hint`
- `raw_metadata_json`

Data quality checks:

- unparseable `published_at`: 0
- empty titles: 0
- empty bodies: 0
- missing source URLs: 0
- duplicate title/source URL pairs: 0
- unmapped source groups: 0

Important limitation: the data is RSS-derived and query-selected. It is useful for operational replay coverage, but it is not a balanced professional news feed.

## Source Group Coverage

The local CSV covers all required source groups, with 12 rows per group:

| Source group | Rows |
|---|---:|
| consumer | 12 |
| credit_financial_conditions | 12 |
| defensive_sectors | 12 |
| energy_commodities | 12 |
| geopolitical | 12 |
| healthcare | 12 |
| inflation_rates | 12 |
| labor | 12 |
| macro_general | 12 |
| manufacturing_industrials | 12 |
| real_estate | 12 |
| technology_ai | 12 |

Source coverage during the capped replay was also broad:

| Source group | Selected replay item count |
|---|---:|
| consumer | 22 |
| credit_financial_conditions | 20 |
| defensive_sectors | 21 |
| energy_commodities | 26 |
| geopolitical | 25 |
| healthcare | 29 |
| inflation_rates | 20 |
| labor | 25 |
| macro_general | 25 |
| manufacturing_industrials | 31 |
| real_estate | 21 |
| technology_ai | 30 |

## Replay Run

Command:

```text
python -m macro_engine.cli replay-news-history --config config/daily_pipeline.yaml --news-file data/news_pilot/news_items_last_30_days.csv --start-date 2026-04-22 --end-date 2026-05-21 --archive --max-items-per-replay-day 10 --mock-ai
```

Replay mode: mock

Replay date range: 2026-04-22 to 2026-05-21

Replay days: 30

Days with news items in the raw CSV window: 27

Days with no raw same-day news items:

- 2026-04-25
- 2026-05-03
- 2026-05-10

Selected replay items:

- minimum per replay day: 5
- maximum per replay day: 10
- total selected across replay days: 295

Classification result:

- successful classifications: 295
- failed classifications: 0
- success rate: 100%
- retry count: 0 in mock mode
- repair count: 0 in mock mode

Run result:

- replay status: success
- replay run count: 30
- failed replay daily runs: 0
- guardrail status: passed for replay daily summaries
- archive behavior: each replay day archived separately under `outputs/archive/<replay-date>/<run_id>/`

## Daily Top Themes

Recurring top macro themes from replay summaries:

| Theme | Daily appearance count |
|---|---:|
| growth_slowdown | 30 |
| monetary_tightening | 23 |
| commodity_pressure | 9 |

This theme mix should be interpreted cautiously because the RSS search set was intentionally constructed to cover macro and sector topics, not to represent a neutral real-time editorial mix.

## Daily Combined Top Sectors

The first combined sector was stable across the replay:

| First combined sector | Days |
|---|---:|
| utilities | 30 |

Latest replay date combined top sectors:

1. utilities
2. health_care
3. consumer_staples
4. consumer_discretionary
5. information_technology

This stability is an operating observation only. It is not evidence of predictive performance.

## Dashboard History

Dashboard export completed after the replay.

`history_index.json` was generated and dashboard data export reported complete data status.

The History tab can display replay runs because replay daily summaries are marked with:

- `run_mode: replay`
- `replay.replay_mode: true`
- `replay.replay_date`

This lets the dashboard distinguish replay runs from live or mock daily runs.

## Date Leakage Check

Replay filtering passed the no-future-news check.

For replay date `D`, the replay selector only allowed items with `published_at` on or before `D`. The final replay used isolated temporary databases per replay day, so previously ingested local news rows from the shared database could not leak into a replay day.

Important caveat: macro data is not vintage in this workflow. The replay simulates historical news-date operation, but macro inputs may still reflect the backend’s currently available macro data unless a separate vintage-data system is added.

## Accumulation And Monitoring

After replay, these commands completed:

```text
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
python -m macro_engine.cli export-dashboard-data
```

Accumulation output:

- run rows: 1
- news history rows: 577
- combined history rows: 309
- readiness label: insufficient_history

Source coverage report:

- configured source groups: 12
- configured sources: 14
- enabled sources: 13
- stored source group count: 10
- unmapped item count: 0
- unmapped percentage: 0.0%
- old item count: 16
- old item percentage: 11.35%

Coverage warnings remain around stale groups and concentration in the latest stored items. That is a data collection quality issue, not a scoring issue.

## Bugs Fixed

Replay database isolation:

- Before: replay daily runs could classify unrelated unclassified rows from the shared local database.
- After: each replay day uses its own temporary DuckDB file, keeping replay-day classification bounded and auditable.

Combined diagnostic empty news-sector handling:

- Before: combined diagnostics could fail with `sector_id` when no sector news score rows existed.
- After: empty news-sector score inputs preserve expected columns and produce macro-only combined diagnostics.

## Guardrails

Generated replay summaries and reports passed the language guardrail scan.

No scoring formulas were changed.

No frontend scoring logic was added.

No frontend AI calls were added.

No allocation, execution, or security-selection logic was added.

## Validation

Final validation completed after the replay and bug fixes:

- `python -m pytest`: 171 passed, 2 skipped
- `python -m ruff check .`: passed
- `python -m macro_engine.cli validate-config`: passed
- `python -m macro_engine.cli export-dashboard-data`: passed
- `npm run build`: passed

## Repo Hygiene

The real-news replay CSV remains local-only.

Generated replay outputs, archives, dashboard exported data, logs, local databases, and caches are not intended to be staged.

Expected local-only paths:

- `data/news_pilot/news_items_last_30_days.csv`
- `outputs/replay/`
- `outputs/archive/`
- `dashboard/public/data/`
- local DuckDB files
- cache folders

## Decision

v0.9-M4 passes.

The 30-day replay executed successfully in bounded mock mode, with 30 archived replay days and no failed daily runs after the replay isolation and empty-news-sector fixes.

v0.9 can proceed to release hardening.

Remaining limitations:

- live AI replay was not run for the full 30-day window;
- RSS-derived inputs are query-selected and may be biased;
- replay history is operational history, not evidence of predictive value;
- macro data is not vintage unless separately supported;
- readiness remains `insufficient_history`.

This is not a trading backtest.

This is not investment advice.

This does not validate predictive performance.
