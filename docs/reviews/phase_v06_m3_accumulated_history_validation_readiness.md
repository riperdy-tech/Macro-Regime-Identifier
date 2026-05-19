# v0.6-M3 Live Daily Pipeline Reliability And Readiness Review

## Verdict

v0.6-M3 passes as a live daily pipeline reliability hardening and accumulated
history readiness review.

Readiness decision:

```text
live daily pipeline reliability: improved
bounded live run: completed
accumulated history: insufficient_history
validation readiness: not ready
next: source group mapping + balanced daily real-news runs
```

This is a readiness review, not a calibration phase. No macro, sector, news, or
combined scoring formulas were changed.

## Reliability Fixes

The prior full live daily run against 120 items hung silently. The likely cause
was the live classification path attempting a long sequential batch with no
daily item cap, no per-item progress output, and no `only_unclassified` resume
behavior. Individual DeepSeek requests already had a configured timeout, but the
daily run could still look silent for a long time while processing many items.

v0.6-M3 added:

```text
- live_ai_safety config for daily pipeline runs
- max live classification items per daily run
- classify-only-unclassified default for live daily runs
- classify-news --max-items
- classify-news --only-unclassified
- classify-news progress output
- incremental per-item classification writes
- failure-rate stop threshold plumbing
- retry backoff configuration
- daily run tests proving live runs pass bounded classification settings
```

The default daily live safety config is:

```yaml
live_ai_safety:
  max_items_per_run: 25
  batch_size: 5
  classify_only_unclassified: true
  continue_on_individual_failure: true
  stop_on_failure_rate_above: 0.20
  stop_on_timeout_count_above: 3
```

The classification service no longer waits until the end of a live batch to
write all results. Each completed item is written to storage before the next
live request starts. If a run is interrupted, completed items remain visible and
the next bounded run can resume with unclassified items.

## Bounded Live Run

Input file:

```text
data/news_pilot/news_items_expanded.csv
```

Input validation:

```text
raw_item_count: 120
unique_item_count: 120
duplicate_count: 0
date_start: 2018-09-23T07:00:00+00:00
date_end: 2026-05-16T11:10:34+00:00
warnings:
  - 1 item has very short body text
  - 16 items are older than one year
```

Bounded live daily command:

```text
python -m macro_engine.cli run-daily-diagnostic --config data/news_pilot/daily_pipeline_expanded_live.yaml --source-profile pilot_expanded_local_csv --live-ai --archive
```

Result:

```text
run_id: 20260519T151554Z-b01dedab
run_date: 2026-05-19
status: success
archive_path: outputs\archive\2026-05-19\20260519T151554Z-b01dedab
warning_count: 0
error_count: 0
```

Classification behavior:

```text
selected_items: 25
mode: only_unclassified
progress: item-by-item
successful_items: 25
failed_items: 0
retry_rate: 2%
repair_rate: 0%
```

The run emitted visible progress:

```text
classify-news: selected 25 item(s) using only-unclassified mode
classify-news: item 1/25 start ...
classify-news: item 1/25 success ... elapsed=5.3s
...
classify-news: item 25/25 success ... elapsed=6.7s
```

The live daily run no longer hangs silently.

## Accumulation Snapshot

After the bounded live pass and accumulation refresh:

```text
raw_items: 126
new_unique_items: 126
duplicate_items: 0
classified_items: 50
failed_items: 0
success_rate: 100%
source_count: 73
source_group_count: 0
date_min: 2018-09-23T15:00:00
date_max: 2026-05-16T19:10:34
readiness_label: insufficient_history
```

The label is honest. The project does not yet have enough balanced real-news
history for validation.

## Source Balance

Source coverage report:

```text
configured_source_count: 14
enabled_source_count: 13
stored_item_count: 126
item_count_by_group:
  macro_general: 6
  unmapped: 120
```

Current issues:

```text
- source_group_count remains 0 in accumulation summary
- 120 real RSS-derived items are unmapped by source group
- 16 expanded-pilot items are older than one year
- most configured source groups have no mapped stored items
- latest source coverage is concentrated in unmapped items
```

Required source groups still need explicit mapping in local real-news files or
RSS watchlist configuration:

```text
inflation_rates
labor
energy_commodities
credit_financial_conditions
real_estate
consumer
manufacturing_industrials
geopolitical
technology_ai
healthcare
defensive_sectors
```

## News Score Behavior

Latest news score date:

```text
2026-05-16
```

Top macro themes:

```text
1. inflation_pressure
2. monetary_tightening
3. energy_supply_shock
4. labor_strength
5. monetary_easing
```

Top sector diagnostic tailwinds:

```text
1. energy
2. financials
3. materials
```

Top sector diagnostic headwinds:

```text
1. real_estate
2. consumer_discretionary
3. utilities
4. industrials
5. consumer_staples
```

This behavior is plausible for the current expanded RSS set, but the source mix
is not yet balanced enough to interpret as representative.

## Combined Diagnostic Stability

Macro-only top sectors in the daily summary:

```text
1. energy
2. materials
3. industrials
4. financials
5. consumer_staples
```

Combined top sectors:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

Overlay behavior:

```text
max_rank_change: 1
guardrail_status: passed
```

The news overlay remained bounded and explainable. It did not overwhelm the
macro sector diagnostic.

## Validation Readiness

Validation is not ready.

Reasons:

```text
- fewer than 100 classified real-news items
- source_group_count is 0
- real-news file contains old RSS-derived items
- source groups are mostly unmapped
- there are not enough repeated daily run dates
- no stable balanced history exists yet
```

No predictive-performance or validation claim is made.

## Tests And Validation

Focused operation tests passed:

```text
python -m pytest tests/test_phase_v05_m1_m2_operations.py tests/test_phase_v06_m1_m2_operations.py
12 passed
```

Final validation commands were run as part of the phase:

```text
python -m pytest
160 passed, 2 skipped

python -m ruff check .
All checks passed

python -m macro_engine.cli validate-config
Config valid: 13 sources, 11 dimensions, 6 regimes
```

Report refresh commands:

```text
python -m macro_engine.cli news-accumulation-summary
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
```

Generated Markdown reports passed the market-action language guardrail scan.

## Repository Hygiene

The phase should stage only source, config, test, and review files.

Do not stage:

```text
.env
data/news_pilot/
outputs/
outputs/archive/
logs/
*.duckdb
caches
```

## Recommendation

Proceed next to:

```text
v0.6-M4: source group mapping + balanced daily real-news runs
```

The key next work is not scoring calibration. It is mapping real-news items to
source groups and running the bounded daily live workflow repeatedly until
history moves from `insufficient_history` toward `early_history`.

This review is not investment advice, not a trading backtest, and not a
validation claim.
