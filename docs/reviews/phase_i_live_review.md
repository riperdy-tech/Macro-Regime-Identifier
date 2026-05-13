# Phase I Live FRED Dry Run Review

Date reviewed: 2026-05-13

Mode: live FRED ingestion with revised-data diagnostic outputs.

This review is an operational and economic sanity check. It is not a point-in-time vintage backtest, investment advice, trading guidance, allocation guidance, or a model calibration report.

## Pipeline Status

Result: pass with warnings.

Latest completed pipeline run:

- Run ID: 2026-05-13T08:57:16.679216+00:00
- Mode: live
- Status: success_with_warnings
- Failed step: none
- Warning count: 53
- Output directory: outputs
- Controlled source set: 10 requested, 10 succeeded
- Generated reports:
  - outputs/current_regime.json
  - outputs/current_regime.md
  - outputs/historical_diagnostic.json
  - outputs/historical_diagnostic.md

Validation after live-run hardening:

- python -m pytest: 70 passed, 2 skipped
- python -m ruff check .: passed
- python -m macro_engine.cli validate-config: passed

## Implementation Findings

The live run exposed and fixed three implementation issues:

- Pipeline environment loading: run-pipeline now loads local .env values before checking for live-mode FRED credentials.
- Source health date handling: timezone-aware fetch timestamps are normalized before comparing with observation dates.
- Dimension/regime build performance: scoring now uses configured input dates instead of repeatedly scanning broad cross-date sets.

Remaining operational concern:

- The full live pipeline completed, but it is still slow at roughly 14 minutes. The largest remaining cost is dimension/regime scoring over large daily tables. Before routine use, the engine should add a proper evaluation calendar and more direct table-driven joins.
- DuckDB local file locking appeared when multiple CLI health commands were launched in parallel against the same database. Sequential reads work. Later hardening should consider read-only connection handling or a documented single-writer/single-reader operational pattern.

## Source Health

The live ingest succeeded for all 10 enabled Phase B source series.

Stale but usable:

- INDPRO: latest observation 2026-03-01, 73 days old, monthly, stale=true
- PCEPI: latest observation 2026-03-01, 73 days old, monthly, stale=true

Fresh:

- BAA10Y
- CPIAUCSL
- DGS10
- FEDFUNDS
- NFCI
- PAYEMS
- T10Y2Y
- UNRATE

Disabled as intended:

- USSLIND: unusable, discontinued_or_stale

## Latest Regime Output

Latest valid regime date: 2026-03-01

Dominant regime: recession

Probability and confidence:

- recession: 0.2867
- stagflation: 0.2204
- tightening: 0.1798
- goldilocks: 0.1694
- reflation: 0.1437
- confidence: 0.0464

Interpretation:

The dominant regime is a low-confidence recession signal, not a strong recession call. The top two regimes are separated by only about 4.6 percentage points, so the model is saying the evidence is ambiguous.

## Dimension Evidence

Latest valid date dimension scores:

- growth_momentum: -0.5212, valid
- inflation_pressure: -0.4605, valid
- policy_stance: -0.0368, valid

Missing at the latest valid regime date:

- credit_liquidity
- yield_curve

Reason:

The engine currently requires same-date dimension availability. Daily/weekly series have fresher observations, while monthly growth and inflation series lag. Because there is no as-of alignment or monthly evaluation calendar yet, the combined valid regime date falls back to the latest date where enough dimensions overlap.

This is the most important Phase I design issue.

## Economic Plausibility

The current recession call is explainable from stored contribution rows:

- Growth momentum is negative and is the main positive contribution to the recession regime.
- Payroll growth and industrial production are below their normalized history.
- Rising unemployment contributes negatively to growth momentum.
- Inflation pressure is negative, so it does not support stagflation strongly.
- Policy stance is close to neutral/slightly opposing the recession score.

Economic verdict:

The explanation is internally consistent, but the output should be treated cautiously because the current regime date is 2026-03-01 and confidence is low. The system is not yet producing a robust "current as of today" macro state.

## Historical Diagnostic

Mode: revised_data

Date range:

- Start: 1990-01-01
- End: 2026-05-12

Summary:

- Valid date count: 434
- Invalid date count: 9178
- Regime switch count: 76
- Average regime duration: 5.64
- Average confidence: 0.0952
- Low-confidence period count: 193

Dominant regime distribution:

- recession: 31.11%
- reflation: 27.88%
- tightening: 18.43%
- goldilocks: 11.52%
- stagflation: 11.06%

Calibration concerns:

- Invalid dates dominate because the diagnostic is operating over a mixed daily/monthly date universe.
- Average confidence is low.
- Recent transitions are low-confidence and somewhat jumpy.
- Recession and reflation dominate the historical distribution, which may be reasonable or may indicate that the current formulas need calibration after the evaluation-calendar issue is fixed.

## Bugs, Data Issues, And Calibration Concerns

Implementation bugs fixed:

- .env loading for live run-pipeline.
- timezone-aware source health comparison.
- excessive dimension/regime scoring loops.

Data issues:

- INDPRO and PCEPI are stale under current thresholds.
- USSLIND remains disabled as designed.

Model/economic calibration concerns:

- Mixed-frequency alignment is not yet good enough for a true current macro read.
- Historical diagnostics should likely run on a monthly evaluation calendar for v0.1.
- Confidence is low across history and current output.
- Regime switching may be too frequent once measured on the correct calendar.

## Recommendation

Do not expand the source universe yet.

Next phase should be operational hardening before calibration:

1. Add an explicit evaluation calendar, likely monthly for v0.1.
2. Add as-of alignment or configured carry-forward rules for daily/weekly/monthly dimensions.
3. Re-run the live pipeline and review whether the latest valid regime date becomes current enough.
4. Only then start calibration review.

