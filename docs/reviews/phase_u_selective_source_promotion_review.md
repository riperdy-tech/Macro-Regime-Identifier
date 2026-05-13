# Phase U Selective Source Promotion Review

Date reviewed: 2026-05-14

Objective: test only the two Phase T promotion candidates, `ICSA` and `BAMLH0A0HYM2`, without mutating production config.

This phase did not add trading logic, allocation logic, ALFRED/vintage backtesting, broad source expansion, `RSAFS`, or `T5YIE`.

## Verdict

Phase U passes as a selective source-promotion test.

Recommendation:

- Promote `ICSA`.
- Promote `BAMLH0A0HYM2`.
- Keep weights modest.
- Document that the current regime label remains low-confidence and sensitive.

The two sources are fresh, usable, economically interpretable, and improve dimension breadth. They do not create invalid dates or near-zero reported transitions. The main caveat is that the current reported regime changes from `stagflation` to `goldilocks` at similarly low confidence, so this should be treated as better measurement rather than a stronger macro call.

## Experiment Setup

Experiment config:

- `config/experiments/phase_u_sources.yaml`

Outputs:

- `outputs/experiments/phase_u/reports/current_regime.json`
- `outputs/experiments/phase_u/reports/current_regime.md`
- `outputs/experiments/phase_u/reports/historical_diagnostic.json`
- `outputs/experiments/phase_u/reports/historical_diagnostic.md`
- `outputs/experiments/phase_u/comparison.json`
- `outputs/experiments/phase_u/comparison.md`

Production config remained unchanged.

## Added Sources

| Series | Feature | Dimension | Status |
|---|---|---|---|
| `ICSA` | `initial_claims_level_z` | `growth_momentum` | fresh, usable |
| `BAMLH0A0HYM2` | `high_yield_oas_level_z` | `credit_liquidity` | fresh, usable |

Excluded from Phase U:

- `RSAFS`
- `T5YIE`

## Source Health

| Series | Last observation | Lag | Status |
|---|---:|---:|---|
| `ICSA` | 2026-05-02 | 11 days | fresh |
| `BAMLH0A0HYM2` | 2026-05-12 | 1 day | fresh |

Feature health:

| Feature | Latest valid date | Valid count | Status |
|---|---:|---:|---|
| `initial_claims_level_z` | 2026-05-02 | 3,045 | usable |
| `high_yield_oas_level_z` | 2026-05-12 | 726 | usable |

Both sources passed the basic operational bar.

## Baseline vs Phase U

| Metric | Phase S baseline | Phase U selective expansion |
|---|---:|---:|
| Series requested | 10 | 12 |
| Latest run series succeeded | 10 | 10 |
| Latest valid regime date | 2026-05-01 | 2026-05-01 |
| Invalid diagnostic dates | 0 | 0 |
| Current reported regime | stagflation | goldilocks |
| Current reported probability | 23.49% | 23.29% |
| Current reported confidence | 3.09% | 2.92% |
| Average confidence | 19.71% | 19.51% |
| Low-confidence periods | 94 | 94 |
| Reported transition count | 44 | 41 |
| Near-zero reported transitions | 0 | 0 |
| Low-confidence reported transitions | 9 | 10 |

Interpretation:

Phase U keeps the system current and valid. It does not improve confidence, but it also does not destabilize the reported timeline. The current label changes, but the probability gap is tiny in both versions, so this is not a strong disagreement; it is a low-confidence boundary case.

## Dimension Coverage

Latest baseline coverage:

| Dimension | Valid feature count |
|---|---:|
| growth_momentum | 3 |
| inflation_pressure | 2 |
| policy_stance | 3 |
| credit_liquidity | 2 |
| yield_curve | 1 |

Latest Phase U coverage:

| Dimension | Valid feature count |
|---|---:|
| growth_momentum | 4 |
| inflation_pressure | 2 |
| policy_stance | 3 |
| credit_liquidity | 3 |
| yield_curve | 1 |

The narrow expansion improves exactly the intended dimensions:

- Growth gets a timely labor-stress feature.
- Credit gets a direct high-yield spread feature.

## Latest Feature Contributions

Phase U latest added-source contributions:

| Feature | Dimension | Normalized value | Polarity | Contribution |
|---|---|---:|---|---:|
| `initial_claims_level_z` | growth_momentum | -0.919 | negative | +0.138 |
| `high_yield_oas_level_z` | credit_liquidity | -1.013 | negative | +0.304 |

Economic interpretation:

- Low jobless claims are supportive for growth momentum.
- Low high-yield spreads are supportive for credit liquidity.

Both signals are intuitive and explain why the Phase U current state becomes more supportive of `goldilocks`.

## Regime Distribution

| Regime | Phase S baseline | Phase U |
|---|---:|---:|
| goldilocks | 10.53% | 12.36% |
| recession | 29.29% | 27.92% |
| reflation | 30.66% | 30.43% |
| stagflation | 11.21% | 9.15% |
| tightening | 18.31% | 20.14% |

The distribution changes are moderate. This does not look like source-induced regime collapse.

## Explanation Quality

Baseline current explanation:

- `stagflation` was supported by weak growth and policy tightness.
- Inflation pressure opposed stagflation because current inflation pressure was below trend.

Phase U current explanation:

- `goldilocks` is supported by credit liquidity and yield curve.
- Growth and policy stance still oppose goldilocks.

This is more nuanced and arguably more informative: the model is separating weaker hard growth data from supportive claims/credit conditions. However, confidence remains low, so the report should not present `goldilocks` as a strong call.

## Risks

Main risk:

- The current reported regime flips at low confidence.

Why this is acceptable:

- The raw and reported probabilities remain visible.
- Confidence remains below 3.2% in both baseline and Phase U.
- Reported transition count falls from 44 to 41.
- Near-zero reported transitions remain zero.
- Dimension coverage improves.

Implementation risk:

- Latest Phase U run still had transient FRED errors, but local stored source health for `ICSA` and `BAMLH0A0HYM2` was clean. This reinforces the need to keep idempotent ingestion and source-health visibility.

## Recommendation for Phase V

Proceed to a controlled production source promotion:

- Add `ICSA`.
- Add `BAMLH0A0HYM2`.
- Do not add `RSAFS`.
- Do not add `T5YIE`.

Suggested production weights:

```yaml
growth_momentum:
  industrial_production_yoy_z: 0.30
  payrolls_yoy_z: 0.30
  unemployment_6m_change_z: 0.25
  initial_claims_level_z: 0.15

credit_liquidity:
  baa_spread_level_z: 0.35
  nfci_level_z: 0.35
  high_yield_oas_level_z: 0.30
```

After promotion, rerun the live pipeline and require:

- Latest valid date remains 2026-05-01 or current month-start.
- Invalid dates remain zero.
- `ICSA` and `BAMLH0A0HYM2` source health are usable.
- Reports clearly show the low-confidence nature of the current regime.

Do not add trading or allocation logic.
