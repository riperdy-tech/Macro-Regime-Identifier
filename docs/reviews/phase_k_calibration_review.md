# Phase K Calibration And Economic Sanity Review

Date reviewed: 2026-05-13

Mode: live FRED pipeline using Phase J monthly calendar-as-of alignment.

This review evaluates the behavior of the existing v0.1 regime formulas. It does not introduce new indicators, trading logic, allocation logic, ALFRED/vintage backtesting, or formula changes.

## Validation

Pre-review checks passed:

- python -m pytest: 77 passed, 2 skipped
- python -m ruff check .: passed
- python -m macro_engine.cli validate-config: passed

Live pipeline result:

- Status: success_with_warnings
- Failed step: none
- Warning count: 3
- Series requested: 10
- Series succeeded: 10
- Latest valid regime date: 2026-05-01
- Output directory: outputs

## Current Regime

Latest valid date: 2026-05-01

Dominant regime: stagflation

Regime probabilities:

- stagflation: 27.44%
- tightening: 23.74%
- reflation: 23.05%
- recession: 14.26%
- goldilocks: 11.51%

Confidence: 3.70%

Interpretation:

This is not a strong stagflation call. The top three regimes are close together, and the model is mostly saying that no single regime is clearly dominant.

## Current Dimension Scores

For 2026-05-01:

- growth_momentum: -0.540, valid, 3/3 features
- inflation_pressure: 1.389, valid, 2/2 features
- policy_stance: -0.170, valid, 3/3 features
- credit_liquidity: 0.609, valid, 2/2 features
- yield_curve: 0.283, valid, 1/1 feature

All dimensions are valid with full coverage. The low-confidence output is therefore not caused by missing dimensions.

## Current Stagflation Drivers

Top supporting dimensions:

- inflation_pressure: +0.417
- growth_momentum: +0.162
- policy_stance: +0.025

Opposing dimensions:

- credit_liquidity: -0.091
- yield_curve: -0.028

Feature-level notes:

- Inflation pressure is mainly driven by pce_price_yoy_z at +3.147, partly offset by headline_cpi_yoy_z at -0.369.
- Growth momentum is weak, led by payrolls_yoy_z at -1.133 and industrial_production_yoy_z at -0.236.
- Credit liquidity is supportive/easy, which correctly opposes stagflation under the current formula.
- Yield curve is positive, which also correctly opposes stagflation.

Economic plausibility:

The current result is internally explainable: high inflation pressure plus weak growth pushes stagflation up, while easy credit and a positive curve push against it. The issue is not incoherent polarity. The issue is that the top regimes remain too close.

## As-Of Data Health

For 2026-05-01, all enabled feature values used by active dimensions are valid under configured lag rules.

Notable lags:

- industrial_production_yoy_z: source observation 2026-03-01, lag 61 days
- pce_price_yoy_z: source observation 2026-03-01, lag 61 days
- unemployment_6m_change_z: source observation 2026-03-01, lag 61 days
- payrolls_yoy_z, CPI, Fed funds: source observation 2026-04-01, lag 30 days
- daily/weekly market and credit series: source observation 2026-05-01, lag 0 days

Data issues:

- INDPRO and PCEPI remain stale at the source-health layer, but usable under the Phase J monthly max-lag rule.
- USSLIND remains disabled as intended.

## Historical Diagnostic

Date range: 1990-01-01 to 2026-05-01

Mode: revised_data

Summary:

- Valid date count: 437
- Invalid date count: 0
- Regime switch count: 65
- Average regime duration: 6.62 months
- Average confidence: 9.70%
- Median confidence: 5.64%
- 25th percentile confidence: 2.74%
- 75th percentile confidence: 11.19%
- Low-confidence periods: 190

Dominant regime distribution:

- reflation: 27.69%
- recession: 23.80%
- tightening: 20.59%
- goldilocks: 15.10%
- stagflation: 12.81%

The historical distribution is not wildly concentrated in one regime, which is good. But confidence is low across a large share of history, which is the main calibration issue.

## Regime Overlap

Recent transitions are mostly low-confidence:

- 2025-05-01: goldilocks -> stagflation, confidence near 0.00
- 2025-06-01: stagflation -> goldilocks, confidence 0.003
- 2025-12-01: goldilocks -> recession, confidence 0.003
- 2026-01-01: recession -> reflation, confidence 0.024
- 2026-03-01: reflation -> stagflation, confidence 0.038

The current top three regimes are also tightly clustered:

- stagflation: 27.44%
- tightening: 23.74%
- reflation: 23.05%

This suggests formula overlap among inflation-heavy regimes. Stagflation, tightening, and reflation can all receive support when inflation pressure is high. The current formulas rely on growth, policy, credit, and curve differences to separate them, but those differences are not producing enough probability distance.

## Suspicious Areas To Review

No implementation bug is obvious from Phase K.

Formula/calibration concerns:

- Probabilities are too flat for much of history.
- Confidence is often very low even when all dimensions are valid.
- Stagflation, tightening, and reflation appear too close in the latest output.
- Goldilocks/recession transitions in 2024-2026 are sometimes extremely low-confidence and may be noise.
- PCEPI is currently dominating inflation_pressure; this may be valid, but it deserves inspection before changing weights.

Dimension polarity concerns:

- No clear polarity bug found.
- Growth weakness supports stagflation and recession as expected.
- High inflation supports stagflation/tightening/reflation as expected.
- Easy credit and a positive yield curve oppose stagflation as expected.
- Policy stance interpretation is plausible, but should be revisited during formula sharpening because tightening and stagflation both use policy in similar ways.

Weight concerns:

- Current regime weights may be too evenly spread across related regimes.
- Inflation receives enough weight to lift several regimes at once, which may be flattening probabilities.
- Recession may need sharper dependence on credit stress and/or curve deterioration rather than only weak growth.
- Stagflation may need stronger asymmetric inflation plus weak-growth interaction rather than additive scoring alone.

## Recommendation

Do not expand sources yet.

Do not use the current output as a strong macro call.

Next recommended phase:

Phase L: controlled calibration experiments.

The first experiments should be:

1. Softmax temperature sensitivity, for example 1.0 vs 0.8 vs 0.6 vs 0.5.
2. Regime formula overlap audit, especially stagflation vs tightening vs reflation.
3. Interaction-style scoring tests for regimes that require combinations, especially stagflation requiring high inflation and weak growth together.
4. Review whether recession should require stronger credit stress and/or curve weakness.
5. Inspect PCEPI's contribution to inflation_pressure before changing inflation weights.

Clear recommendation category:

Revise regime formulas and test softmax temperature. Do not revise source universe yet. Dimension polarities appear broadly correct; formula separation is the next blocker.

