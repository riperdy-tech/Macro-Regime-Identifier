# Phase M Production Temperature Update

Date reviewed: 2026-05-13

Objective: promote the Phase L calibration result by changing the production/default regime softmax temperature from 1.0 to 0.6.

This phase did not change regime formulas, feature formulas, dimension weights, source universe, trading logic, allocation logic, or ALFRED/vintage backtesting.

## Change

Previous production temperature:

- softmax_temperature: 1.0

New production temperature:

- softmax_temperature: 0.6

Config updated:

- config/phase_b_sources.yaml

Phase L rationale:

- Temperature 0.6 improved probability separation without changing raw-score rankings or regime transitions in the controlled experiment run.
- Formula variants were not promoted.

## Validation

Validation after the config update:

- python -m pytest: 80 passed, 2 skipped
- python -m ruff check .: passed
- python -m macro_engine.cli validate-config: passed

Live pipeline:

- Command: python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
- Final run status: success_with_warnings
- Series requested: 10
- Series succeeded: 10
- Series failed: 0
- Expected stale sources: INDPRO, PCEPI
- Latest valid regime date: 2026-05-01

One prior live attempt during this phase received transient FRED HTTP 500 errors for 6 series. A rerun succeeded with 10/10 series. No code or config change was made in response to that provider-side transient.

## Current Output

Latest valid date: 2026-05-01

Dominant regime: stagflation

Current probabilities after production temperature update:

- stagflation: 31.60%
- reflation: 25.24%
- tightening: 24.82%
- recession: 10.41%
- goldilocks: 7.93%

Current confidence: 6.36%

Dimension scores on 2026-05-01:

- growth_momentum: -0.540
- inflation_pressure: 1.389
- policy_stance: -0.170
- credit_liquidity: 0.722
- yield_curve: 0.283

All dimensions were valid with full coverage.

## Before And After

Baseline from Phase K / Phase L at temperature 1.0:

- Dominant regime: stagflation
- Current probability: 27.44%
- Current confidence: 3.70%
- Average historical confidence: 9.70%
- Median historical confidence: 5.64%
- Low-confidence periods: 190
- Regime switch count: 65

Production after temperature 0.6:

- Dominant regime: stagflation
- Current probability: 31.60%
- Current confidence: 6.36%
- Average historical confidence: 17.23%
- Median historical confidence: 11.11%
- Low-confidence periods: 101
- Regime switch count: 63

Note on switch count:

The controlled Phase L temperature-only experiment preserved the 65-switch historical sequence on the same stored data. The Phase M live rerun used refreshed FRED data and produced 63 switches. This is not evidence that temperature changed raw-score rankings; it reflects the live data refresh state after rerunning ingestion.

## Confirmation Of Scope

Unchanged:

- Source universe
- Feature transforms
- Normalization functions
- Dimension weights
- Regime formulas
- Phase L formula variants
- Historical diagnostic mode
- Trading/allocation behavior

Changed:

- Production/default regime probability softmax temperature only

## Verdict

Phase M passes.

Temperature 0.6 should be treated as the v0.1 candidate production setting. It improves probability separation while preserving the same broad economic read: the current regime remains low-to-moderate confidence stagflation, not a strong macro call.

Recommended next phase:

Phase N: formula design review only.

Focus:

- stagflation vs tightening
- recession vs stagflation

Do not expand sources until formula overlap is reviewed.

