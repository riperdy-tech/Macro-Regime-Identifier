# v0.5-M2 Real-News Accumulation Tracker Review

## Verdict

v0.5-M2 passes.

The milestone adds accumulation tracking for stored news items, classifications,
news score history, and combined diagnostic history. It does not perform
validation or tune scoring assumptions.

## Implemented

Added config:

```text
config/news_accumulation.yaml
```

Added tables:

```text
news_accumulation_runs
news_score_history_summary
combined_diagnostic_history_summary
```

Added commands:

```text
python -m macro_engine.cli run-news-accumulation --config config/news_accumulation.yaml
python -m macro_engine.cli news-accumulation-summary
python -m macro_engine.cli write-news-accumulation-report --config config/news_accumulation.yaml
```

Generated report:

```text
outputs/news_accumulation_report.json
outputs/news_accumulation_report.md
```

## How Accumulation Is Tracked

The tracker summarizes stored outputs that already exist in the local database:

```text
news_items
news_classifications
news_daily_theme_scores
news_daily_sector_scores
combined_sector_diagnostics
sector_scores
```

It records:

```text
raw item count
new unique item count
duplicate item count
classified item count
failed item count
classification success rate
source count
source-group count
news score history by date
combined rank-change history by date
```

## Readiness Labels

Readiness labels are operational coverage labels:

```text
insufficient_history
early_history
monitor_ready
validation_candidate
```

Rules:

```text
insufficient_history: fewer than 5 run dates or fewer than 100 classified items
early_history: 5 to 20 run dates
monitor_ready: 20+ run dates with reasonable source coverage
validation_candidate: 60+ run dates with stable source coverage
```

These labels are not predictive validation claims.

## Current Accumulated History Status

Latest local accumulation summary:

```text
raw item count: 6
new unique items: 6
duplicate items: 0
classified items: 6
failed items: 0
success rate: 100.0%
source count: 1
source-group count: 0
readiness label: insufficient_history
```

News score history:

```text
news history rows: 5
combined history rows: 5
```

The current history is synthetic/mock sample history, not enough for validation.

## Real Data Status

The project has support for real local news pilot data under ignored paths such
as:

```text
data/news_pilot/
```

The release validation used mock/synthetic data for reproducibility. Real-news
accumulation should use local pilot files and live AI only when intentionally
enabled.

## Validation Status

Validation remains blocked by insufficient balanced real-news history.

The correct next operational step is repeated daily collection and monitoring,
not score tuning.

## Tests

Focused v0.5 tests:

```text
tests/test_phase_v05_m1_m2_operations.py
5 passed
```

Final validation:

```text
python -m pytest
153 passed, 2 skipped

python -m ruff check .
passed

python -m macro_engine.cli validate-config
Config valid: 13 sources, 11 dimensions, 6 regimes
```

## Known Limitations

```text
current accumulated sample is too small
source-group coverage is absent in synthetic examples
readiness labels are simple thresholds
no empirical validation is performed
real source balance still depends on local pilot data quality
```

## Next Milestone Recommendation

If the daily pipeline remains stable, v0.5-M3 should be release hardening.

If source coverage remains weak after several daily runs, v0.5-M3 should focus
on source adapter expansion or better balanced local collection.

This is not investment advice, a trading system, an allocation system, or a
trading backtest.
