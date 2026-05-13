# Phase O Formula Experiments

Date reviewed: 2026-05-13

Objective: run targeted formula experiments from the Phase N design review without changing production formulas.

This phase did not expand sources, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or change production config. Experiments used stored Phase M calendar-aligned dimension scores.

## Experiment Setup

Experiment config:

- config/experiments/phase_o.yaml

Command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_o.yaml
```

Outputs:

- outputs/experiments/phase_o/baseline.json
- outputs/experiments/phase_o/tightening_growth_resilience.json
- outputs/experiments/phase_o/stagflation_interaction_reduced_additive.json
- outputs/experiments/phase_o/reflation_inflation_cap.json
- outputs/experiments/phase_o/recession_growth_confirmation.json
- outputs/experiments/phase_o/policy_tightening_heavy.json
- outputs/experiments/phase_o/policy_stagflation_less_policy.json
- outputs/experiments/phase_o/combined_overlap_reduction.json
- outputs/experiments/phase_o/comparison.json
- outputs/experiments/phase_o/comparison.md

Production regime outputs were not overwritten.

## Baseline

Production baseline:

- softmax_temperature: 0.6
- current dominant regime: stagflation
- current probability: 31.60%
- current confidence: 6.36%
- average confidence: 17.23%
- median confidence: 11.11%
- low-confidence periods: 101
- regime switches: 63
- average regime duration: 6.83 months

Baseline dominant regime distribution:

- reflation: 29.29%
- recession: 24.94%
- tightening: 21.28%
- goldilocks: 12.59%
- stagflation: 11.90%

Baseline raw-score correlations:

- stagflation vs tightening: 0.673
- recession vs stagflation: 0.517
- reflation vs stagflation: -0.399
- reflation vs tightening: 0.116

## Variant Results

### Tightening Growth Resilience

Design:

Tightening growth_momentum changed from positive_only to positive, so weak growth actively penalizes tightening instead of merely contributing zero.

Results:

- current probability: 32.40%
- current confidence: 6.52%
- average confidence: 18.46%
- median confidence: 12.39%
- low-confidence periods: 91
- regime switches: 67
- stagflation vs tightening correlation: 0.419
- recession vs stagflation correlation: 0.517

Assessment:

This directly improves the main overlap, but it increases switches from 63 to 67. It is promising, but the higher switch count should be reviewed before promotion.

### Stagflation Interaction Reduced Additive

Design:

Stagflation standalone inflation/growth weights were reduced and an inflation x weak-growth interaction was added.

Results:

- current probability: 29.31%
- current confidence: 3.23%
- average confidence: 18.25%
- median confidence: 11.52%
- low-confidence periods: 111
- regime switches: 64
- stagflation vs tightening correlation: 0.724
- recession vs stagflation correlation: 0.482

Assessment:

This reduced recession/stagflation overlap but worsened stagflation/tightening overlap and weakened current separation. Do not promote this version.

### Reflation Inflation Cap

Design:

Reflation inflation_pressure changed to reward_near_zero, reducing support from extreme inflation.

Results:

- current probability: 37.65%
- current confidence: 8.08%
- average confidence: 16.33%
- median confidence: 10.85%
- low-confidence periods: 113
- regime switches: 66
- stagflation vs tightening correlation: 0.673
- recession vs stagflation correlation: 0.517
- reflation vs stagflation correlation: -0.719
- reflation vs tightening correlation: -0.155

Assessment:

This improves current stagflation separation from reflation and sharply separates reflation from inflation-stress regimes. But it lowers average confidence, increases low-confidence periods, and raises switches. Useful idea, not a standalone promotion candidate.

### Recession Growth Confirmation

Design:

Recession uses weak-growth interactions with credit stress and yield-curve weakness.

Results:

- current probability: 31.25%
- current confidence: 6.29%
- average confidence: 15.18%
- median confidence: 10.63%
- low-confidence periods: 116
- regime switches: 69
- stagflation vs tightening correlation: 0.673
- recession vs stagflation correlation: 0.605

Assessment:

This worsens the secondary overlap, lowers confidence, and increases switching. Do not promote.

### Policy Tightening Heavy

Design:

Tightening requires growth resilience and places more weight on policy_stance.

Results:

- current probability: 33.14%
- current confidence: 6.67%
- average confidence: 19.24%
- median confidence: 13.20%
- low-confidence periods: 88
- regime switches: 67
- average regime duration: 6.43 months
- stagflation vs tightening correlation: 0.391
- recession vs stagflation correlation: 0.517
- reflation vs tightening correlation: 0.281

Assessment:

This is the strongest single variant. It reduces the main overlap most, improves average and median confidence, and lowers low-confidence periods. The main drawback is switch count rising from 63 to 67, and reflation/tightening correlation increasing.

### Policy Stagflation Less Policy

Design:

Stagflation relies less on direct policy benefit and more on inflation/growth.

Results:

- current probability: 33.59%
- current confidence: 9.09%
- average confidence: 17.29%
- median confidence: 12.52%
- low-confidence periods: 89
- regime switches: 63
- stagflation vs tightening correlation: 0.565
- recession vs stagflation correlation: 0.599

Assessment:

This has the best current confidence and does not increase switches. However, it only partially improves stagflation/tightening overlap and worsens recession/stagflation overlap. Do not promote alone, but the reduced stagflation policy weight is worth combining carefully with other ideas.

### Combined Overlap Reduction

Design:

Combines tightening growth resilience, stagflation interaction, reflation cap, and recession confirmation.

Results:

- current probability: 35.75%
- current confidence: 7.60%
- average confidence: 16.85%
- median confidence: 11.21%
- low-confidence periods: 106
- regime switches: 63
- stagflation vs tightening correlation: 0.482
- recession vs stagflation correlation: 0.582
- reflation vs stagflation correlation: -0.709

Assessment:

The combined variant improves current confidence and keeps switch count stable, but it worsens recession/stagflation overlap and shifts the historical distribution heavily toward tightening and recession while reducing stagflation and goldilocks. Do not promote as-is.

## Best-Performing Variant

Best single variant:

- policy_tightening_heavy

Why:

- strongest reduction in stagflation vs tightening correlation: 0.673 to 0.391
- average confidence improves: 17.23% to 19.24%
- median confidence improves: 11.11% to 13.20%
- low-confidence periods fall: 101 to 88
- current confidence improves slightly: 6.36% to 6.67%

Concerns:

- regime switches increase from 63 to 67
- reflation vs tightening correlation rises from 0.116 to 0.281

Best current-month confidence:

- policy_stagflation_less_policy

Why not promote:

- recession vs stagflation correlation worsens from 0.517 to 0.599

Best reflation separation:

- reflation_inflation_cap

Why not promote:

- average confidence falls and low-confidence periods rise

## Did The Main Separations Improve?

### Stagflation vs Tightening

Yes, several variants improved this.

Best:

- policy_tightening_heavy: 0.673 to 0.391
- tightening_growth_resilience: 0.673 to 0.419
- combined_overlap_reduction: 0.673 to 0.482

Interpretation:

The Phase N diagnosis was correct. Tightening needs growth resilience and stronger policy dependence to separate from stagflation.

### Recession vs Stagflation

No strong improvement.

Best tested:

- stagflation_interaction_reduced_additive: 0.517 to 0.482

But that variant worsened stagflation/tightening overlap and current confidence.

Worse variants:

- recession_growth_confirmation: 0.517 to 0.605
- policy_stagflation_less_policy: 0.517 to 0.599
- combined_overlap_reduction: 0.517 to 0.582

Interpretation:

The recession confirmation design tested here is not good enough. Recession/stagflation separation needs a different approach, likely stronger inflation gating for recession rather than more credit/curve interaction.

## Did Confidence Improve?

Yes for some variants.

Best average confidence:

- policy_tightening_heavy: 19.24%
- tightening_growth_resilience: 18.46%
- stagflation_interaction_reduced_additive: 18.25%

Baseline:

- 17.23%

Best low-confidence count:

- policy_tightening_heavy: 88
- policy_stagflation_less_policy: 89
- tightening_growth_resilience: 91

Baseline:

- 101

Current confidence:

- baseline: 6.36%
- policy_tightening_heavy: 6.67%
- policy_stagflation_less_policy: 9.09%
- reflation_inflation_cap: 8.08%
- combined_overlap_reduction: 7.60%

Interpretation:

Confidence improves, but the cleanest structural improvement is not the same as the highest current-month confidence. The best model-design signal is policy_tightening_heavy.

## Did Switching Become Worse?

Mixed.

No switch increase:

- policy_stagflation_less_policy: 63
- combined_overlap_reduction: 63

Moderate switch increase:

- tightening_growth_resilience: 67
- policy_tightening_heavy: 67
- reflation_inflation_cap: 66

Worse:

- recession_growth_confirmation: 69

Baseline:

- 63

Interpretation:

The best structural variant increases switching modestly. That is not an automatic blocker, but it needs transition review before any production promotion.

## Recommendation

Do not promote any formula variant directly to production yet.

Recommended next step:

Run a Phase P targeted follow-up experiment combining the best ideas more narrowly:

1. Tightening growth resilience plus policy-heavy tightening.
2. Reflation inflation cap, but with a less punitive cap than reward_near_zero.
3. Recession inflation gating rather than recession credit/curve interaction.
4. Optional stagflation policy-weight reduction, but only if it does not worsen recession/stagflation overlap.

Candidate for further testing:

- policy_tightening_heavy

Not recommended:

- recession_growth_confirmation
- stagflation_interaction_reduced_additive as currently specified
- combined_overlap_reduction as currently specified

Production recommendation:

Keep production formulas unchanged for now. Keep softmax_temperature at 0.6. Do not expand sources yet.

