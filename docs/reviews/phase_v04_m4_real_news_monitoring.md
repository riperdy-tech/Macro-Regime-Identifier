# v0.4-M4 Real-News Monitoring Review

## Verdict

v0.4-M4 passes.

This milestone adds a repeatable monitoring layer for real-news input quality,
AI classification quality, and combined overlay stability. It does not change
macro formulas, sector assumptions, news scoring formulas, or combined overlay
weights.

## Monitoring Infrastructure Added

Added config:

```text
config/news_monitoring.yaml
```

Added source profiles:

```text
pilot_balanced_local_csv
pilot_balanced_local_json
```

Expected balanced pilot path:

```text
data/news_pilot/news_items_balanced.csv
```

Added tables:

```text
news_input_quality_runs
news_classification_quality_runs
news_overlay_monitoring
```

Added CLI commands:

```text
python -m macro_engine.cli validate-news-monitoring --config config/news_monitoring.yaml
python -m macro_engine.cli run-news-monitoring --config config/news_monitoring.yaml
python -m macro_engine.cli news-monitoring-summary
python -m macro_engine.cli write-news-monitoring-report --config config/news_monitoring.yaml
```

Generated report:

```text
outputs/news_monitoring_report.json
outputs/news_monitoring_report.md
```

## Balanced Pilot Data

No balanced real-news pilot file was committed or required for tests.

The monitoring config documents the expected local path:

```text
data/news_pilot/news_items_balanced.csv
```

Because no committed balanced file is expected, the release validation used the
existing synthetic sample profile in mock mode. This is sufficient to validate
the monitoring workflow, but not to evaluate balanced real-news behavior.

## Input Quality Summary

Latest monitoring run:

```text
Profile: synthetic_sample
Raw items: 6
Unique items: 6
Duplicate count: 0
Source count: 1
Date range: 2026-05-01 to 2026-05-05
Short body count: 6
Old item count: 0
Future item count: 0
Quality status: warning
```

Warnings were expected:

```text
synthetic sample has short body text
source count is below configured minimum
one source exceeds configured concentration threshold
```

This confirms monitoring detects sample/source imbalance instead of treating it
as clean real-news coverage.

## Classification Quality Summary

Latest monitoring run:

```text
Total items: 6
Successful classifications: 6
Failed classifications: 0
Success rate: 100.0%
Retry count: 0
Retry rate: 0.0%
Repaired count: 0
Repair rate: 0.0%
Provider/model: mock / mock-news-classifier
Quality status: ok
```

This run used mock mode for release safety. The live M3 pilot remains the latest
larger live-AI reliability check:

```text
M1 live success rate: 32 / 40 = 80%
M3 live success rate: 120 / 120 = 100%
```

## News Overlay Behavior

Latest monitoring run:

```text
Diagnostic date: 2026-05-05
News item count in combined overlay: 3
Max rank change: 0
Average absolute rank change: 0.000
Thin news warning: true
Overlay status: warning
```

Top news themes:

```text
monetary_tightening
commodity_pressure
growth_slowdown
```

Sector diagnostic tailwind:

```text
energy
```

Sector diagnostic headwind:

```text
real_estate
```

The overlay remained bounded and did not change the latest sector rank ordering.
The warning is appropriate because synthetic sample news is thin.

## Bias Findings

The monitoring layer can now surface:

```text
source concentration
theme concentration
sector concentration
date concentration
old item contamination
overlay rank-change pressure
thin news coverage
```

Current validation intentionally shows source concentration in the synthetic
sample. A balanced real-news file is still needed to evaluate real production
coverage.

## Scoring Formula Decision

News scoring formulas remain unchanged.

The correct next action is to accumulate more balanced real-news history before
tuning news weights, source weights, freshness decay, or combined overlay
weights.

## Limitations

```text
synthetic sample is not real-news validation
balanced pilot file is local-only and not committed
monitoring is operational QA, not empirical validation
live AI was not required for this release validation run
source-group quality depends on local pilot data metadata
```

## M5 Readiness

v0.4-M4 finds no serious release blocker.

The monitoring report generates, quality issues are surfaced rather than hidden,
classification quality is measurable, and overlay stability is tracked. v0.4 can
proceed to conditional release hardening as an experimental real-news monitoring
overlay.

This is not investment advice, a trading system, an allocation system, or a
trading backtest.
