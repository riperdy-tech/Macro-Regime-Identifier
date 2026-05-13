# Phase L Calibration Experiments

Date reviewed: 2026-05-13

Mode: experiments run against stored Phase J calendar-aligned dimension scores.

This review compares calibration variants only. It does not expand sources, replace production formulas, add trading logic, add allocation logic, or implement ALFRED/vintage backtesting.

## Validation

Pre-experiment validation:

- python -m pytest: 77 passed, 2 skipped
- python -m ruff check .: passed
- python -m macro_engine.cli validate-config: passed

Phase L focused validation:

- tests/test_phase_l_experiments.py: 3 passed
- ruff: passed

Experiment command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_l.yaml
```

Outputs:

- outputs/experiments/phase_l/baseline.json
- outputs/experiments/phase_l/temperature_0_8.json
- outputs/experiments/phase_l/temperature_0_6.json
- outputs/experiments/phase_l/temperature_0_5.json
- outputs/experiments/phase_l/sharper_stagflation.json
- outputs/experiments/phase_l/sharper_tightening.json
- outputs/experiments/phase_l/sharper_reflation.json
- outputs/experiments/phase_l/stronger_recession_credit_curve.json
- outputs/experiments/phase_l/combined_formula_sharpening.json
- outputs/experiments/phase_l/comparison.json
- outputs/experiments/phase_l/comparison.md

Production regime outputs were not overwritten.

## Baseline

Latest valid date: 2026-05-01

Current dominant regime: stagflation

Current probabilities:

- stagflation: 27.44%
- tightening: 23.74%
- reflation: 23.05%
- recession: 14.26%
- goldilocks: 11.51%

Baseline metrics:

- Current confidence: 3.70%
- Average historical confidence: 9.70%
- Median historical confidence: 5.64%
- 25th percentile confidence: 2.74%
- 75th percentile confidence: 11.19%
- Low-confidence periods: 190
- Regime switches: 65
- Average regime duration: 6.62 months

Baseline dominant regime distribution:

- reflation: 27.69%
- recession: 23.80%
- tightening: 20.59%
- goldilocks: 15.10%
- stagflation: 12.81%

## Temperature Experiments

Temperature changes preserve raw-score rankings and regime transitions while changing probability concentration.

### Temperature 0.8

- Current dominant regime: stagflation
- Current probability: 29.26%
- Current confidence: 4.85%
- Average confidence: 12.53%
- Median confidence: 7.53%
- Low-confidence periods: 149
- Regime switches: 65

### Temperature 0.6

- Current dominant regime: stagflation
- Current probability: 32.22%
- Current confidence: 6.91%
- Average confidence: 17.14%
- Median confidence: 10.93%
- Low-confidence periods: 104
- Regime switches: 65

### Temperature 0.5

- Current dominant regime: stagflation
- Current probability: 34.49%
- Current confidence: 8.68%
- Average confidence: 20.68%
- Median confidence: 13.83%
- Low-confidence periods: 80
- Regime switches: 65

Temperature verdict:

Lowering temperature materially improves probability separation without changing the underlying regime sequence. Temperature 0.6 looks like the best first candidate: it improves confidence meaningfully while avoiding the more aggressive concentration of 0.5.

## Formula Experiments

### Sharper Stagflation

This variant added an inflation x weak-growth interaction and reduced additive stagflation support.

- Current probability: 26.93%
- Current confidence: 3.02%
- Average confidence: 10.53%
- Median confidence: 5.96%
- Low-confidence periods: 183
- Regime switches: 65

This did not improve the current separation. It slightly improved average confidence but made stagflation more correlated with tightening.

### Sharper Tightening

This variant emphasized inflation plus restrictive policy and penalized weak growth.

- Current probability: 27.26%
- Current confidence: 3.02%
- Average confidence: 10.42%
- Median confidence: 6.34%
- Low-confidence periods: 172
- Regime switches: 65

This improved low-confidence count modestly but weakened current separation.

### Sharper Reflation

This variant required growth/liquidity support and penalized excessive inflation.

- Current probability: 30.68%
- Current confidence: 4.14%
- Average confidence: 9.25%
- Median confidence: 5.58%
- Low-confidence periods: 193
- Regime switches: 63

This helps current stagflation beat reflation more clearly, but it lowers average historical confidence and does not solve the broader flat-probability problem.

### Stronger Recession Credit/Curve

This variant gave recession more weight on credit stress and yield-curve weakness.

- Current probability: 27.47%
- Current confidence: 3.71%
- Average confidence: 9.03%
- Median confidence: 5.57%
- Low-confidence periods: 200
- Regime switches: 72

This is worse than baseline. It increases switching and lowers average confidence.

### Combined Formula Sharpening

This variant combined formula changes and temperature 0.8.

- Current probability: 32.49%
- Current confidence: 3.51%
- Average confidence: 12.99%
- Median confidence: 7.62%
- Low-confidence periods: 143
- Regime switches: 62
- Average regime duration: 6.94 months

This reduces switches and improves average confidence, but current confidence remains lower than the simple temperature 0.6 variant. It also shifts historical distribution toward recession and reflation, which needs more economic review before adoption.

## Raw-Score Correlations

Baseline selected correlations:

- stagflation vs tightening: 0.686
- recession vs stagflation: 0.558
- reflation vs tightening: 0.094
- reflation vs stagflation: -0.442
- goldilocks vs recession: -0.886

Main interpretation:

The most important overlap is stagflation vs tightening, followed by recession vs stagflation. Reflation is not highly correlated with tightening in raw-score terms, but it can remain close in probability during the latest period because its raw score is similar at the current date.

Formula variants did not cleanly solve the overlap. The combined variant lowered stagflation vs tightening correlation to 0.634 and reflation vs tightening to 0.015, but raised recession vs stagflation to 0.622.

## Implementation Bugs

One experiment-harness bug was found and fixed during Phase L:

- The experiment runner initially passed date columns directly into diagnostic builders without the same normalization used by production diagnostics. This caused experiment summaries to report zero valid dates even when regime health was valid. The runner now normalizes dates before building timelines.

No production model bug was found.

## Data Issues

No new data issue was found in Phase L.

Known Phase J live data caveats still apply:

- INDPRO and PCEPI are stale at the source-health layer but valid under the monthly as-of lag policy.
- USSLIND remains disabled as intended.

## Economic And Model Concerns

The model remains structurally valid but under-separated.

Key concerns:

- Baseline probabilities are too flat.
- Temperature adjustments improve separation more reliably than the tested formula variants.
- Stagflation and tightening remain too close when inflation is high.
- Recession and stagflation have meaningful positive raw-score correlation.
- Formula variants need more careful economic design before replacing production defaults.

## Recommendation

Recommended default change for the next controlled implementation phase:

Set softmax_temperature to 0.6 for v0.1 candidate testing.

Do not adopt the formula variants yet.

Reason:

- Temperature 0.6 raises current confidence from 3.70% to 6.91%.
- Average confidence rises from 9.70% to 17.14%.
- Median confidence rises from 5.64% to 10.93%.
- Low-confidence periods fall from 190 to 104.
- Regime switches and dominant regime distribution are unchanged because raw-score rankings are preserved.

Formula work should continue, but the tested formula variants are not clearly superior to a modest temperature adjustment.

Clear recommendation category:

Adjust temperature first, then run a second formula-design phase focused specifically on stagflation vs tightening and recession vs stagflation separation. Do not expand sources yet.
