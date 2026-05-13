# Phase P Targeted Formula Experiments

Date reviewed: 2026-05-13

Objective: run a narrower follow-up formula experiment phase based on Phase O findings. Production formulas were not changed.

This phase did not expand sources, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or change production config. Experiments used stored Phase M/J calendar-aligned dimension scores.

## Experiment Setup

Experiment config:

- config/experiments/phase_p.yaml

Command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_p.yaml
```

Outputs:

- outputs/experiments/phase_p/baseline.json
- outputs/experiments/phase_p/policy_tightening_heavy_v2.json
- outputs/experiments/phase_p/tightening_growth_resilience_lighter.json
- outputs/experiments/phase_p/reflation_soft_inflation_cap.json
- outputs/experiments/phase_p/recession_inflation_gate.json
- outputs/experiments/phase_p/recession_inflation_gate_lighter.json
- outputs/experiments/phase_p/policy_stagflation_less_policy_combined.json
- outputs/experiments/phase_p/combined_best_candidate.json
- outputs/experiments/phase_p/comparison.json
- outputs/experiments/phase_p/comparison.md

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

Baseline correlations:

- stagflation vs tightening: 0.673
- recession vs stagflation: 0.517
- reflation vs tightening: 0.116
- reflation vs stagflation: -0.399

Baseline transition review:

- near-zero-confidence transitions: 8
- latest notable low-confidence transition: 2025-12-01, goldilocks to reflation, confidence 0.007

## Variant Results

### Policy Tightening Heavy v2

Design:

Policy-heavy tightening with growth resilience, but slightly less aggressive than the Phase O policy_tightening_heavy variant.

Results:

- current probability: 32.84%
- current confidence: 6.61%
- average confidence: 19.15%
- median confidence: 13.33%
- low-confidence periods: 87
- regime switches: 65
- average duration: 6.62 months
- stagflation vs tightening correlation: 0.440
- recession vs stagflation correlation: 0.517
- reflation vs tightening correlation: 0.258
- near-zero-confidence transitions: 13

Assessment:

This is the best targeted variant. It preserves most of Phase O's tightening/stagflation separation improvement while reducing switch-count pressure from 67 to 65. It improves confidence and low-confidence periods materially versus baseline. The main concern is more near-zero-confidence transitions and higher reflation/tightening correlation.

### Tightening Growth Resilience Lighter

Design:

Lighter growth-resilience tightening variant intended to reduce switching.

Results:

- current probability: 32.47%
- current confidence: 6.54%
- average confidence: 18.71%
- median confidence: 12.94%
- low-confidence periods: 88
- regime switches: 69
- stagflation vs tightening correlation: 0.518
- recession vs stagflation correlation: 0.517
- near-zero-confidence transitions: 16

Assessment:

This is worse than policy_tightening_heavy_v2. It reduces the key correlation less and increases switching more.

### Reflation Soft Inflation Cap

Design:

Reflation penalizes strongly positive inflation and rewards growth/liquidity.

Results:

- current probability: 37.04%
- current confidence: 7.95%
- average confidence: 17.91%
- median confidence: 12.23%
- low-confidence periods: 105
- regime switches: 51
- stagflation vs tightening correlation: 0.673
- recession vs stagflation correlation: 0.517
- reflation vs tightening correlation: -0.316
- reflation vs stagflation correlation: -0.839
- near-zero-confidence transitions: 7

Assessment:

This sharply separates reflation from inflation-stress regimes and reduces switches, but it severely distorts the historical distribution: goldilocks falls to less than 1% and reflation rises above 37%. Do not promote as-is.

### Recession Inflation Gate

Design:

Recession more strongly penalizes high inflation without adding the failed Phase O credit/curve interactions.

Results:

- current probability: 32.08%
- current confidence: 6.46%
- average confidence: 16.45%
- median confidence: 11.11%
- low-confidence periods: 105
- regime switches: 64
- stagflation vs tightening correlation: 0.673
- recession vs stagflation correlation: 0.487
- near-zero-confidence transitions: 7

Assessment:

This is the best recession/stagflation separation variant, and it avoids the Phase O failure mode of raising recession/stagflation correlation. But it lowers average confidence and increases low-confidence periods. It is useful as a component, not a production-ready variant.

### Recession Inflation Gate Lighter

Design:

Lighter recession inflation gate.

Results:

- current probability: 31.85%
- current confidence: 6.41%
- average confidence: 16.44%
- median confidence: 11.25%
- low-confidence periods: 109
- regime switches: 64
- recession vs stagflation correlation: 0.549

Assessment:

This is worse than the stronger recession inflation gate and worse than baseline on the secondary overlap. Do not promote.

### Policy Stagflation Less Policy Combined

Design:

Combines policy-heavy tightening with a smaller direct policy role in stagflation.

Results:

- current probability: 34.27%
- current confidence: 8.60%
- average confidence: 19.09%
- median confidence: 13.81%
- low-confidence periods: 89
- regime switches: 67
- stagflation vs tightening correlation: 0.339
- recession vs stagflation correlation: 0.582
- near-zero-confidence transitions: 10

Assessment:

This has the strongest stagflation/tightening separation and strong confidence improvement, but it worsens recession/stagflation overlap materially. Do not promote unless recession/stagflation is addressed.

### Combined Best Candidate

Design:

Combines policy-heavy tightening v2, soft reflation inflation cap, and lighter recession inflation gate.

Results:

- current probability: 39.15%
- current confidence: 13.09%
- average confidence: 19.04%
- median confidence: 15.06%
- low-confidence periods: 83
- regime switches: 51
- stagflation vs tightening correlation: 0.440
- recession vs stagflation correlation: 0.549
- reflation vs tightening correlation: -0.066
- reflation vs stagflation correlation: -0.839
- near-zero-confidence transitions: 7

Assessment:

This looks attractive on confidence and switches, but it distorts the historical distribution too much. Goldilocks falls below 1%, reflation rises to 36.84%, and tightening rises to 26.77%. It is not production-worthy as-is.

## Best Candidate

Best production candidate from Phase P:

- policy_tightening_heavy_v2

Why:

- improves the primary overlap: stagflation vs tightening correlation 0.673 to 0.440
- improves average confidence: 17.23% to 19.15%
- improves median confidence: 11.11% to 13.33%
- lowers low-confidence periods: 101 to 87
- increases switches only modestly: 63 to 65
- does not worsen recession vs stagflation overlap
- keeps historical distribution broadly plausible

Concerns:

- near-zero-confidence transitions rise from 8 to 13
- reflation vs tightening correlation rises from 0.116 to 0.258
- current confidence improves only modestly, from 6.36% to 6.61%

## Promotion Decision

Do not promote immediately.

Phase P found a credible production candidate, but the transition review shows more near-zero-confidence transitions. The candidate should be promoted only after a transition-focused review or one very small smoothing/transition filter experiment.

## Source Expansion Decision

Do not begin broad source expansion yet.

Reason:

The formula layer now has a credible candidate, but the engine still needs one more decision:

- either promote policy_tightening_heavy_v2 after transition review
- or add a minimal transition-confidence filter before promotion

Source expansion should wait until that formula decision is settled.

## Recommendation

Recommended next phase:

Phase Q: transition-focused review of policy_tightening_heavy_v2.

Phase Q should not be a broad formula experiment phase. It should inspect:

- the 13 near-zero-confidence transitions in policy_tightening_heavy_v2
- whether the extra switches are economically reasonable
- whether a simple minimum confidence/gap transition filter is needed
- whether policy_tightening_heavy_v2 can be promoted as the v0.1 formula candidate

Current production recommendation:

- keep production formulas unchanged
- keep softmax_temperature at 0.6
- do not expand sources yet

