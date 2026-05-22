# v1.1-M2 Source Coverage Improvement

Verdict: pass.

This milestone improved real-news source coverage metadata and ran a bounded
daily diagnostic after the source-profile update. No scoring formulas,
dashboard scoring behavior, or model logic were changed.

## Source Coverage Before

Before M2, stored source coverage looked like this:

```text
stored_item_count: 141
source_group_count: 10
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 17
old_item_pct: 12.1%
```

Missing data groups:

```text
defensive_sectors
technology_ai
```

Latest-item concentration:

```text
latest stored items were concentrated in inflation_rates
```

## Source Coverage After

After adding the mapped last-30-days local profile and refreshing reports:

```text
stored_item_count: 270
source_group_count: 12
unmapped_item_count: 0
unmapped_pct: 0.0%
old_item_count: 17
old_item_pct: 6.3%
```

Missing data groups:

```text
none
```

All required source groups now have stored items:

```text
consumer
credit_financial_conditions
defensive_sectors
energy_commodities
geopolitical
healthcare
inflation_rates
labor
macro_general
manufacturing_industrials
real_estate
technology_ai
```

## Sources Added Or Updated

Added to `config/news_sources.yaml`:

```text
last_30_days_local_csv
profiles:
  - last_30_days_local_csv
  - last_30_days_real_news
  - mapped_30_day_replay
path: data/news_pilot/news_items_last_30_days.csv
```

Updated `config/news_source_watchlist.yaml` so the local daily group entries
point at the mapped last-30-days file until a refreshed daily file is supplied:

```text
data/news_pilot/news_items_last_30_days.csv
```

The real CSV remains local-only and unstaged.

## Source Validation

Command:

```powershell
python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml
```

Result:

```text
valid: true
configured_source_count: 14
enabled_source_count: 13
configured groups: 12
```

Input validation for the new profile:

```text
profile: last_30_days_local_csv
raw_item_count: 144
unique_item_count: 144
source_group_count: 12
unmapped_item_count: 0
unmapped_pct: 0.0%
date_start: 2026-04-21
date_end: 2026-05-21
```

Input warning:

```text
141 items have very short body text
```

That warning reflects RSS-derived snippets and should remain visible.

## Remaining Coverage Warnings

The latest source coverage report still warns:

```text
some source groups have stale stored items
latest stored items are concentrated in a small number of source groups
```

Groups needing fresher coverage:

```text
consumer
credit_financial_conditions
energy_commodities
labor
technology_ai
```

This is a data collection issue, not a scoring issue.

## Daily Run Result After Update

Command:

```powershell
python -m macro_engine.cli run-daily-diagnostic --config data/news_pilot/daily_pipeline_expanded_live.yaml --source-profile last_30_days_local_csv --live-ai --archive
```

Result:

```text
run_id: 20260522T160646Z-c6fd30d6
run_date: 2026-05-22
status: success
selected live classification items: 25
successful classifications: 25
failed classifications: 0
archive_path: outputs\archive\2026-05-22\20260522T160646Z-c6fd30d6
guardrail_status: passed
```

The daily pipeline completed cleanly with bounded live classification and
archive creation.

## Dashboard And Source Coverage Behavior

Dashboard export:

```text
data_status: complete
missing_files: none
latest_run_date: 2026-05-22
latest_macro_date: 2026-05-01
latest_news_score_date: 2026-05-21
```

The History tab includes the latest daily run and shows:

```text
status: success
readiness_label: insufficient_history
guardrail_status: passed
```

The final dashboard refresh run was `20260522T162258Z-c2bc0817`, which selected
0 new rows because the mapped profile had already been classified. The run that
added new live classifications was `20260522T160646Z-c6fd30d6`.

Source coverage now reports all 12 source groups represented and 0 unmapped
items.

## Enough History?

No.

The classified item count improved, and source group coverage improved, but the
system still lacks enough repeated real daily run dates. The readiness label
remains:

```text
insufficient_history
```

## Recommended Next Step

Continue operations.

Recommended next milestone:

```text
v1.1-M3: continued real daily runs and freshness review
```

Focus areas:

- run the daily workflow across separate calendar/trading days;
- keep using bounded live classification;
- refresh stale source groups;
- monitor short-body RSS snippet quality;
- do not tune scoring until enough real history exists.

## Boundary Statement

This review is not investment advice.

This system is not a trading system.

This review does not validate predictive performance.
