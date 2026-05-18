# v0.4-M5 Release Hardening Review

## Verdict

v0.4-M5 passes.

Release decision:

```text
v0.4 is release-ready as an experimental real-news monitoring overlay.
```

This release candidate does not change v0.1 macro scoring, v0.2 sector scoring,
or v0.3 news scoring/combined formulas. It adds monitoring, documentation, and
release hygiene around real-news input quality and overlay stability.

## What v0.4 Adds

v0.4 adds:

```text
news monitoring config
balanced local pilot profiles
input quality monitoring
classification quality monitoring
combined overlay stability monitoring
news monitoring JSON/Markdown report
release checklist for v0.4
updated README and model limitations
```

New tables:

```text
news_input_quality_runs
news_classification_quality_runs
news_overlay_monitoring
```

New commands:

```text
validate-news-monitoring
run-news-monitoring
news-monitoring-summary
write-news-monitoring-report
```

## What v0.4 Does Not Do

v0.4 does not:

```text
change macro formulas
change sector assumptions
tune news scoring formulas
add security selection
add execution logic
add portfolio sizing
add ALFRED/vintage backtesting
turn the overlay into an empirically validated strategy
```

## Validation Results

Final validation:

```text
python -m pytest
148 passed, 2 skipped

python -m ruff check .
passed

python -m macro_engine.cli validate-config
Config valid: 13 sources, 11 dimensions, 6 regimes
```

Full workflow validation:

```text
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
status: success_with_warnings
series requested: 12
series succeeded: 12
latest valid regime date: 2026-05-01
dominant regime: reflation
confidence: 16.41%
```

The macro pipeline warnings were stale-source warnings for slower monthly
series. They are expected operational warnings, not release blockers.

Other workflow commands completed:

```text
build-sector-scores
write-sector-report
ingest-news
classify-news
write-news-report
build-news-scores
write-news-score-report
build-combined-sector-diagnostics
write-combined-sector-report
write-news-monitoring-report
write-sector-validation-report
```

## Current Macro Output

Latest macro snapshot:

```text
Date: 2026-05-01
Raw dominant regime: reflation
Reported regime: reflation
Raw probability: 40.11%
Confidence: 16.41%
Transition filter reason: raw_signal_confirmed
```

Regime probabilities:

```text
reflation: 40.11%
tightening: 23.70%
stagflation: 18.08%
goldilocks: 12.61%
recession: 5.50%
```

## Current Sector Ranking

Latest v0.2 sector macro ranking:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
6. health_care
7. consumer_discretionary
8. communication_services
9. information_technology
10. real_estate
11. utilities
```

## Latest News Monitoring Summary

The release validation used mock mode and the committed synthetic sample profile.

Input quality:

```text
Profile: synthetic_sample
Raw items: 6
Unique items: 6
Duplicate count: 0
Source count: 1
Quality status: warning
```

The warning is expected because the synthetic sample has one source and short
bodies. Monitoring correctly flags this as thin/unbalanced input rather than
pretending it is balanced real-news coverage.

Classification quality:

```text
Total items: 6
Success count: 6
Failure count: 0
Success rate: 100.0%
Retry count: 0
Retry rate: 0.0%
Repaired count: 0
Repair rate: 0.0%
Provider/model: mock / mock-news-classifier
Quality status: ok
```

Latest larger live-AI pilot from v0.4-M3 remains:

```text
120 / 120 live classifications succeeded
success rate: 100.0%
```

## Combined Sector Diagnostic

Latest combined diagnostic date:

```text
2026-05-05
```

Combined top sectors:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
```

Monitoring summary:

```text
News item count: 3
Max rank change: 0
Average absolute rank change: 0.000
Thin news warning: true
Overlay status: warning
```

The warning is appropriate for the synthetic sample. The news overlay remained
bounded and did not change the latest sector ranking.

## Live AI Status

Live AI was not required for v0.4 release validation.

The v0.4-M3 pilot already tested live DeepSeek classification on an expanded
real-news set and achieved 120/120 successful classifications after prompt and
schema hardening. v0.4-M5 uses mock mode by default so release validation is
reproducible and does not require a live API key.

No API key is written into tracked files.

## Report Guardrails

Generated Markdown reports were audited for forbidden market-action language:

```text
outputs/*.md
```

Result:

```text
passed
```

A pre-existing sector validation disclaimer used stricter-audit forbidden terms.
The wording was revised to neutral diagnostic language and the report was
regenerated.

## Repository Hygiene

`git status --short --ignored` showed only intended tracked changes plus ignored
local artifacts:

```text
.env ignored
data/news_pilot/ ignored
outputs/ ignored
DuckDB files ignored
caches ignored
```

No secrets, pilot data, generated reports, local databases, or caches are staged.

## Known Limitations

```text
balanced real-news pilot file is local-only and not committed
synthetic sample is useful for release validation but not empirical validation
source/query balance must be monitored over time
old RSS result contamination remains a real pilot risk
classification repair/retry rates must be watched in live mode
combined overlay is still experimental
news scoring calibration remains deferred
```

## Release Blockers

None.

## Non-Blocking Follow-Ups

```text
collect balanced real-news history over time
track live classification quality over repeated runs
monitor source and theme concentration
review old RSS item contamination
only tune news scoring after enough balanced real-news history exists
```

## Final Release Decision

v0.4 is ready to tag as:

```text
v0.4-rc1
```

Release positioning:

```text
experimental real-news monitoring and AI news diagnostic overlay
```

This is not investment advice, a trading system, an allocation system, a
security recommendation system, or a trading backtest.
