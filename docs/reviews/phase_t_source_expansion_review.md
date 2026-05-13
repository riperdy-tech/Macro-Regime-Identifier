# Phase T Source Expansion Review

Date reviewed: 2026-05-14

Objective: test a small source expansion on top of the Phase S v0.1 model core without mutating production config.

This phase did not add trading logic, allocation logic, ALFRED/vintage backtesting, or broad source expansion.

## Verdict

Phase T passes as an experiment, but the full four-source basket should not be promoted as-is.

Recommendation:

- Candidate for promotion: `ICSA`
- Candidate for promotion: `BAMLH0A0HYM2`
- Hold for another test: `RSAFS`
- Do not promote yet: `T5YIE`

The expanded run improved dimension breadth and did not destabilize the reported timeline, but the source basket was not clean enough for immediate production promotion. `T5YIE` remained unavailable in the experiment database after repeated live FRED attempts, and `RSAFS` was stale at the latest evaluation date.

## Tested Sources

Experimental config:

- `config/experiments/phase_t_sources.yaml`

Added sources:

| Series | Role | Feature | Dimension |
|---|---|---|---|
| `ICSA` | Initial jobless claims | `initial_claims_level_z` | `growth_momentum` |
| `RSAFS` | Retail and food services sales | `retail_sales_yoy_z` | `growth_momentum` |
| `T5YIE` | 5-year breakeven inflation rate | `five_year_breakeven_level_z` | `inflation_pressure` |
| `BAMLH0A0HYM2` | High-yield option-adjusted spread | `high_yield_oas_level_z` | `credit_liquidity` |

Production config remained unchanged.

## Live Run Notes

FRED returned intermittent HTTP 500 errors during the experiment. The baseline was rerun until all 10 production sources succeeded. The expanded run retained enough cached/previously fetched data for valid outputs, but the latest ingestion attempt still had failed series.

This matters for promotion: the model can tolerate transient ingestion errors once local storage has data, but a new source should not be promoted unless its source-health behavior is reliable.

## Baseline vs Expanded

| Metric | Phase S baseline | Phase T expanded |
|---|---:|---:|
| Series requested | 10 | 14 |
| Latest run series succeeded | 10 | 7 |
| Latest valid regime date | 2026-05-01 | 2026-05-01 |
| Current reported regime | stagflation | goldilocks |
| Current reported probability | 23.49% | 23.47% |
| Current reported confidence | 3.09% | 3.13% |
| Valid diagnostic dates | 437 | 437 |
| Invalid diagnostic dates | 0 | 0 |
| Average confidence | 19.71% | 19.55% |
| Low-confidence periods | 94 | 93 |
| Reported transition count | 44 | 39 |
| Near-zero reported transitions | 0 | 0 |
| Low-confidence reported transitions | 9 | 8 |

Interpretation:

The expanded set did not create instability. Reported transitions fell from 44 to 39, near-zero transitions stayed at zero, and invalid dates stayed at zero. However, confidence did not materially improve, and the current reported regime changed from `stagflation` to `goldilocks` with almost the same low confidence. That is not a promotion-grade improvement by itself.

## Source Health

Baseline stale sources:

- `INDPRO`
- `PCEPI`

Expanded stale sources:

- `INDPRO`
- `PCEPI`
- `RSAFS`
- `T5YIE`

Expanded unusable sources:

- `T5YIE`
- `USSLIND` disabled health-test source

Expanded feature health:

- `initial_claims_level_z`: usable
- `retail_sales_yoy_z`: usable, but latest source observation stale
- `five_year_breakeven_level_z`: unusable, missing source data
- `high_yield_oas_level_z`: usable

## Dimension Coverage

Latest baseline dimension coverage:

| Dimension | Valid features | Status |
|---|---:|---|
| growth_momentum | 3 | valid |
| inflation_pressure | 2 | valid |
| policy_stance | 3 | valid |
| credit_liquidity | 2 | valid |
| yield_curve | 1 | valid |

Latest expanded dimension coverage:

| Dimension | Valid features | Status |
|---|---:|---|
| growth_momentum | 5 | valid |
| inflation_pressure | 2 of 3 | valid |
| policy_stance | 3 | valid |
| credit_liquidity | 3 | valid |
| yield_curve | 1 | valid |

The expansion improved growth and credit dimension robustness. Inflation did not improve because `T5YIE` was unavailable and its weight was renormalized away.

## Current Explanation Quality

Baseline current report:

- Reported regime: `stagflation`
- Explanation was driven by weak growth and policy tightness, with inflation pressure opposing stagflation because current inflation pressure z-score was negative.

Expanded current report:

- Reported regime: `goldilocks`
- Explanation was driven by supportive credit liquidity and a positive yield curve.
- Growth and policy stance opposed the regime.

The expanded explanation is not obviously wrong, but it is not clearly better. It mostly says the model is uncertain: the top five probabilities are clustered between 16.86% and 23.47%.

## Source-by-Source Judgment

### ICSA

Judgment: promote candidate.

Reasons:

- Fresh weekly source.
- Adds timely labor-market stress information.
- Improves growth dimension breadth.
- Economic interpretation is clear: higher claims weaken growth momentum; lower claims support it.

Risk:

- Claims can be noisy weekly. Keep it as a modest-weight feature.

### BAMLH0A0HYM2

Judgment: promote candidate.

Reasons:

- Fresh daily source.
- Adds a better credit-risk spread than BAA-only credit pressure.
- Improved credit dimension breadth from 2 to 3 valid features.
- Economic interpretation is clean: higher high-yield spreads imply tighter credit/risk stress.

Risk:

- Credit spread indicators can overlap with existing BAA spread and NFCI. Keep weight moderate.

### RSAFS

Judgment: hold for another test.

Reasons:

- Adds useful consumer-demand information.
- Feature was usable in this run.
- Latest observation was stale under current health rules.

Risk:

- Retail sales are revised and can be noisy. It may still be valuable, but should be retested when source freshness is clean.

### T5YIE

Judgment: do not promote yet.

Reasons:

- Source was unusable in the expanded run.
- `five_year_breakeven_level_z` had missing source data.
- It did not improve inflation dimension coverage.

Risk:

- Inflation expectations are conceptually useful, but this specific source needs a clean ingestion run before consideration. A later test could retry `T5YIE`, compare `T10YIE`, or use another inflation-expectations proxy.

## Recommendation

Do not promote the full Phase T source basket.

Recommended next step:

Run one narrower source-expansion follow-up with:

- `ICSA`
- `BAMLH0A0HYM2`

Optional second lane:

- Retest `RSAFS` only if source freshness is acceptable.
- Retry `T5YIE` or test `T10YIE` as an inflation-expectations proxy, but do not include it in the main promotion candidate until ingestion is clean.

Promotion bar remains:

- Latest valid regime date stays current.
- Invalid dates remain zero.
- Source health is clean or warnings are explainable.
- Dimension coverage improves.
- Reported transitions do not become unstable.
- Explanation quality improves without forcing a large low-confidence regime flip.

The model core is stable enough for controlled source expansion, but source promotion should remain selective.
