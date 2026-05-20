# v0.6-M4 Source Group Mapping And Balanced Runs Review

Verdict: pass as an operational source-quality milestone.

v0.6-M4 improved source group mapping and completed a bounded live daily run
against mapped real-news pilot data. This is still not a validation milestone.
The system is more observable and better mapped, but accumulated history remains
too thin and uneven for empirical claims.

## Scope

This milestone did not change macro, sector, news, or combined scoring formulas.
It did not add trading, allocation, execution, portfolio sizing, or security
selection logic.

## Source Group Mapping Added

The news ingestion config now supports audited source group mapping rules in
`config/news_sources.yaml`.

Mapping precedence:

1. Explicit `source_group` column in local CSV/JSON.
2. Explicit `query_group` when it matches an allowed source group.
3. Configured `source_group_rules`.
4. Fallback to `unmapped`.

The rule layer records mapping metadata such as `source_group_mapping_rule` and
`source_group_mapping_method` in raw item metadata. This keeps mapped pilot data
auditable and avoids silently pretending weak mappings are facts.

## Data Quality Thresholds

The source coverage and monitoring configs now track:

- unmapped item count and percentage
- source group count
- single-group concentration
- old item count and percentage
- missing groups
- stale groups

Configured thresholds:

- `max_unmapped_pct: 0.20`
- `min_source_groups: 3`
- `max_single_group_pct: 0.50`
- `max_old_item_pct: 0.20`

## Balanced Data Availability

A local-only mapped pilot file was created:

```text
data/news_pilot/news_items_balanced.csv
```

It was generated from the existing expanded RSS-derived pilot file and includes
explicit `source_group` metadata. It is ignored by git and was not staged.

This file is mapped, but not fully balanced. It represents seven source groups,
with inflation/rates and labor still heavily represented.

## Source Coverage Before And After

Before M4, the M3 readiness review showed:

```text
source_group_count: 0
unmapped real RSS items: 120
```

After M4, the latest source coverage report shows:

```text
stored_item_count: 126
source_group_count: 7
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 16
old_item_pct: 12.7%
```

Stored item count by mapped group:

```text
inflation_rates: 44
labor: 23
energy_commodities: 18
credit_financial_conditions: 15
macro_general: 14
real_estate: 9
geopolitical: 3
```

Groups still missing stored data:

```text
consumer
defensive_sectors
healthcare
manufacturing_industrials
technology_ai
```

The latest-date coverage is concentrated in `inflation_rates`, so source
balance remains a non-blocking limitation.

## Bounded Live Daily Run

Command:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config data/news_pilot/daily_pipeline_expanded_live.yaml --source-profile pilot_balanced_local_csv --live-ai --archive
```

Result:

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

The run emitted per-item progress and completed cleanly. It used the bounded
live classification path with `only_unclassified` behavior.

## Classification Quality

Accumulation snapshot after the mapped live pass:

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

The latest daily summary showed:

```text
retry_rate: 1.33%
repair_rate: 0.0%
guardrail_status: passed
```

## News Score Behavior

Latest news score date:

```text
2026-05-16
```

Top macro news themes:

```text
inflation_pressure
monetary_tightening
energy_supply_shock
labor_strength
monetary_easing
```

Top sector diagnostic tailwinds:

```text
energy
financials
materials
```

Top sector diagnostic headwinds:

```text
real_estate
consumer_discretionary
industrials
utilities
consumer_staples
```

This behavior is plausible for the current RSS-derived data, but the data is
not broad enough to support scoring calibration.

## Combined Diagnostic Behavior

Latest combined sector ranking:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

The overlay remained bounded:

```text
max_rank_change: 1
news did not overwhelm macro ranking
```

## Readiness Label

Readiness remains:

```text
insufficient_history
```

Reasons:

- only one effective real pilot run date
- 75 classified real-news items, below validation-ready history
- five required groups still missing stored data
- latest-date coverage remains concentrated
- old RSS items remain present

## Release Hardening Decision

M4 can proceed to v0.6-M5 release hardening because:

- source group mapping is now auditable
- unmapped share improved from 120 items to 0%
- bounded live operation completed cleanly
- reports remain diagnostic
- no scoring formulas changed

Non-blocking limitations:

- source coverage is still incomplete
- latest-date source concentration remains high
- repeated daily history is still insufficient

This review is not investment advice. It is not a trading backtest. It makes no
predictive validation claim.
