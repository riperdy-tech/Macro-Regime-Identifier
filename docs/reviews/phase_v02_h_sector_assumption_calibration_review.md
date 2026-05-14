# v0.2-H Sector Assumption Calibration Review

Date: 2026-05-15

## Verdict

v0.2-H passes as a review-only calibration diagnostic. No sector exposure, prior, macro formula, source, or scoring config was changed.

The v0.2-G3 validation infrastructure works, but the empirical signal is weak and mixed. The available ETF proxy CSV is described as synthetic sample data, so these results should be treated primarily as a plumbing and methodology check, not as final market evidence.

Recommendation: run targeted v0.2-I calibration experiments before v0.2 release hardening. Do not add news, trading logic, allocation logic, portfolio sizing, security recommendations, or ALFRED/vintage backtesting.

## v0.2-G3 Summary

| Horizon | Observations | Rank IC | Top Avg Relative Return | Bottom Avg Relative Return | Top Minus Bottom | Top Positive Hit Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 836 | 0.0122 | 0.0002 | 0.0029 | -0.0027 | 0.5066 |
| 3m | 814 | 0.0439 | 0.0068 | 0.0049 | 0.0019 | 0.5541 |

The 3-month horizon is more promising than the 1-month horizon. It has a higher rank IC, a positive top-minus-bottom spread, and a better top-positive hit rate. The signal is still small.

## Raw vs Confidence-Adjusted Scores

Raw sector scores and confidence-adjusted scores produce identical rank validation metrics in the current implementation because the confidence multiplier is date-level and applies equally to all sectors on the same date.

This means confidence adjustment is useful for report magnitude and caution language, but it does not improve cross-sector ranking validation. It should stay in reports, but v0.2-I should not expect the current multiplier to fix ranking weakness.

## Sector-Level Diagnostics

| Sector | Avg Adjusted Score | Avg 1m Relative Return | Avg 3m Relative Return | 1m Hit Rate | 3m Hit Rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| energy | 0.1412 | -0.0011 | -0.0026 | 0.4474 | 0.4595 |
| consumer_staples | 0.1252 | -0.0014 | -0.0057 | 0.4737 | 0.5135 |
| health_care | 0.0592 | 0.0006 | 0.0068 | 0.4474 | 0.4730 |
| utilities | 0.0422 | -0.0018 | -0.0084 | 0.4737 | 0.4595 |
| materials | -0.0161 | -0.0008 | -0.0017 | 0.5132 | 0.5541 |
| communication_services | -0.1059 | -0.0008 | -0.0043 | 0.4605 | 0.4730 |
| industrials | -0.1182 | 0.0016 | 0.0093 | 0.5263 | 0.5270 |
| real_estate | -0.1238 | -0.0029 | -0.0100 | 0.4474 | 0.4324 |
| financials | -0.1503 | 0.0021 | 0.0095 | 0.4737 | 0.5135 |
| information_technology | -0.1692 | 0.0013 | 0.0066 | 0.5263 | 0.5135 |
| consumer_discretionary | -0.2567 | 0.0023 | 0.0052 | 0.5000 | 0.5135 |

The most suspicious sector-level pattern is that energy and consumer staples carry the highest average scores but negative average forward relative returns in the sample. Meanwhile consumer discretionary, information technology, financials, and industrials have negative average scores but positive average relative returns.

Because the input price file is synthetic sample data, this should not be treated as proof the assumptions are wrong. It does indicate which assumptions should be stressed once real ETF data is available.

## Component-Level Diagnostics

Average absolute contribution by component:

| Component Type | Component | Avg Abs Contribution | 1m IC | 3m IC |
| --- | --- | ---: | ---: | ---: |
| dimension_exposure | policy_stance | 0.3620 | 0.0361 | 0.0344 |
| dimension_exposure | inflation_pressure | 0.2765 | -0.0092 | 0.0131 |
| dimension_exposure | growth_momentum | 0.2358 | 0.0140 | 0.0226 |
| dimension_exposure | yield_curve | 0.2236 | 0.0116 | 0.0080 |
| dimension_exposure | credit_liquidity | 0.2212 | 0.0298 | 0.0850 |
| regime_prior | recession | 0.0449 | -0.0022 | -0.0117 |
| regime_prior | reflation | 0.0428 | 0.0024 | 0.0071 |
| regime_prior | tightening | 0.0403 | -0.0221 | -0.0052 |
| regime_prior | stagflation | 0.0383 | -0.0205 | -0.0204 |
| regime_prior | goldilocks | 0.0186 | 0.0132 | 0.0154 |

Dimension exposure contributions dominate the score. Policy stance is the largest component by average absolute contribution. Inflation pressure is also large, but its validation relationship is weak to negative at 1 month and only slightly positive at 3 months.

Credit liquidity has the strongest 3-month component relationship in this sample. Regime priors are much smaller than dimension exposures and look slightly noisy overall.

## Likely Weakness Sources

1. Data limitation: The available ETF proxy file is synthetic sample data. It is good enough to validate the workflow, date alignment, and reporting, but not enough for final empirical calibration.
2. Horizon mismatch: The 3-month horizon is more promising than 1 month. The sector mapper may be too slow-moving for 1-month validation.
3. Regime priors may add noise: Aggregate regime-prior contribution has slightly negative or near-zero validation relationship, while dimension exposures are small but more positive.
4. Inflation assumptions may be too strong: Energy and consumer staples rank high on average but do not validate well in the sample. Inflation pressure is one of the largest components but has weak validation.
5. Rate/yield penalties may be too harsh for growth sectors: Information technology and consumer discretionary are scored negatively on average but show positive average relative returns in the sample.
6. Real estate weakness is directionally consistent: Real estate has negative average scores and negative forward relative returns, so rate/credit sensitivity may be broadly reasonable there.

## Suspicious Assumptions to Test

- Reduce standalone inflation-pressure exposure for energy and consumer staples.
- Rebalance information technology and consumer discretionary so growth momentum can offset policy/rate pressure more often.
- Test whether utilities should be treated as defensive only in recession-like regimes rather than penalized broadly by policy and yield-curve pressure.
- Test whether regime priors should be downweighted relative to dimension exposures.
- Test a 3-month-primary validation objective rather than treating 1-month and 3-month as equally important.
- Test component caps so one macro dimension, especially policy stance or inflation pressure, cannot dominate sector scores.

## Recommended v0.2-I Experiments

Run a narrow sector calibration experiment phase with production assumptions unchanged.

Candidate variants:

1. Dimension-heavy variant: reduce regime-prior weight by 50% and compare validation metrics.
2. Inflation-softened variant: reduce inflation sensitivity for energy, consumer staples, information technology, and consumer discretionary.
3. Growth-offset variant: increase growth sensitivity for information technology, consumer discretionary, financials, and industrials.
4. Rate-penalty-softened variant: reduce policy and yield-curve penalties for technology and utilities while leaving real estate mostly unchanged.
5. Component-cap variant: cap each dimension contribution before summing sector scores.
6. 3-month objective review: rank variants primarily by 3-month rank IC and top-minus-bottom spread, while ensuring 1-month behavior does not become severely worse.

Promotion bar for any variant should be strict:

- Better 3-month rank IC and top-minus-bottom spread.
- No materially worse 1-month result.
- No sector ranking that is dominated by a single dimension.
- Diagnostic language remains unchanged.
- No trading, allocation, or recommendation logic.

## Release Recommendation

Do not proceed directly to v0.2 release hardening as if the sector mapper is empirically validated. The sector layer is structurally sound and explainable, but the assumptions need targeted calibration review with either real ETF price data or clearly labeled sample-data experiments.

