# v0.5-M1 Daily Operating Pipeline Review

## Verdict

v0.5-M1 passes.

The milestone adds a repeatable daily operating command that runs the existing
macro, sector, news, combined, and monitoring workflows, records a run row,
writes a daily summary, audits report language, and archives generated outputs.
It does not change scoring formulas.

## Implemented

Added config:

```text
config/daily_pipeline.yaml
```

Added table:

```text
daily_diagnostic_runs
```

Added command:

```text
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml
```

Added outputs:

```text
outputs/daily_diagnostic_summary.json
outputs/daily_diagnostic_summary.md
outputs/archive/YYYY-MM-DD/<run_id>/
```

## Default Behavior

The daily pipeline is mock-safe for news by default:

```text
news source profile: synthetic_sample
live AI: disabled unless explicitly configured
archive: enabled
guardrail audit: enabled
```

The macro step still uses the configured macro pipeline. In the local release
run, live macro ingestion was available and completed.

## Daily Command Example

```text
python -m macro_engine.cli run-daily-diagnostic --config config/daily_pipeline.yaml --mock-ai --archive
```

Latest local run:

```text
run_id: 20260518T160706Z-81396fc6
run_date: 2026-05-18
status: success
archive_path: outputs/archive/2026-05-18/20260518T160706Z-81396fc6
warning_count: 0
error_count: 0
```

## Run Table Behavior

`daily_diagnostic_runs` records:

```text
run_id
started_at
completed_at
status
run_date
macro_status
sector_status
news_ingestion_status
news_classification_status
news_scoring_status
combined_status
monitoring_status
guardrail_status
archive_path
warnings_json
errors_json
```

## Archive Behavior

The daily runner copies generated JSON/Markdown reports into:

```text
outputs/archive/YYYY-MM-DD/<run_id>/
```

Generated output directories remain ignored by git.

## Daily Summary

The daily summary includes:

```text
run date and status
macro regime and confidence
top sector macro diagnostics
top news themes
top sector news diagnostic tailwinds/headwinds
combined top sectors
classification success/retry/repair rates
overlay rank-change monitoring
generated artifact paths
archive path
diagnostic-only disclaimer
```

Latest summary snapshot:

```text
macro regime: reflation
macro confidence: 16.4%
top sector diagnostics: energy, materials, financials
top news themes: monetary_tightening, commodity_pressure
combined top sectors: energy, materials, financials
overlay max rank change: 0
```

## Guardrail Behavior

The daily runner audits generated Markdown reports for forbidden market-action
language and records `guardrail_status`.

Tests cover both:

```text
guardrail pass
guardrail failure recorded as failed run
```

## Tests

Focused v0.5 tests:

```text
tests/test_phase_v05_m1_m2_operations.py
5 passed
```

Final validation is recorded in the v0.5-M2 review after both bundled parts.

## Known Limitations

```text
daily pipeline still depends on local source/profile configuration
live AI requires intentional local config and local API key
archives are local generated artifacts, not source-controlled records
mock/synthetic news validates operations, not real-world signal quality
```

## M2 Readiness

M1 has no blocker. The daily pipeline works in mock/synthetic mode and can
proceed to accumulation tracking.

This is not investment advice, a trading system, an allocation system, or a
trading backtest.
