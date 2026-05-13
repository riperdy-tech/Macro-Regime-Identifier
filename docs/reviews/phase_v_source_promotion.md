# Phase V Source Promotion

Date reviewed: 2026-05-14

Objective: promote the two Phase U source candidates into production:

- `ICSA`
- `BAMLH0A0HYM2`

This phase did not promote `RSAFS` or `T5YIE`, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or broaden the source universe beyond the approved two sources.

## Verdict

Phase V passes as a controlled production source promotion.

Production now includes:

- `ICSA` -> `initial_claims_level_z` -> `growth_momentum`
- `BAMLH0A0HYM2` -> `high_yield_oas_level_z` -> `credit_liquidity`

Production still excludes:

- `RSAFS`
- `T5YIE`

## Production Changes

Added sources:

| Series | Frequency | Stale threshold | Unusable threshold |
|---|---|---:|---:|
| `ICSA` | weekly | 14 days | 35 days |
| `BAMLH0A0HYM2` | daily | 5 days | 15 days |

Added features:

| Feature | Transform | Normalization | Direction |
|---|---|---|---|
| `initial_claims_level_z` | level | rolling_z_5y | higher_is_growth_negative |
| `high_yield_oas_level_z` | level | rolling_z_5y | higher_is_credit_tight |

Updated dimension weights:

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

Unchanged:

- `softmax_temperature: 0.6`
- Phase S tightening formula
- `transition_filter.enabled: true`
- `transition_filter.min_confidence_to_switch: 0.02`
- Regime formulas
- Trading/allocation logic

## Live Pipeline

Command:

```powershell
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
```

Result:

- Status: `success_with_warnings`
- Series requested: 12
- Series succeeded: 12
- Stale series: `INDPRO`, `PCEPI`
- Latest valid regime date: 2026-05-01
- Invalid diagnostic dates: 0

The warnings are explainable and not caused by the promoted sources.

## Promoted Source Health

| Series | Last observation | Lag | Usable | Reason |
|---|---:|---:|---|---|
| `ICSA` | 2026-05-02 | 11 days | true | fresh |
| `BAMLH0A0HYM2` | 2026-05-12 | 1 day | true | fresh |

Promoted feature health:

| Feature | Latest valid date | Valid count | Usable |
|---|---:|---:|---|
| `initial_claims_level_z` | 2026-05-02 | 3,045 | true |
| `high_yield_oas_level_z` | 2026-05-12 | 726 | true |

## Current Regime

Latest valid date: 2026-05-01

Raw monthly signal:

| Regime | Probability |
|---|---:|
| reflation | 29.79% |
| stagflation | 28.76% |
| tightening | 22.90% |
| goldilocks | 9.36% |
| recession | 9.19% |

Raw dominant regime: `reflation`

Raw confidence: 1.03%

Reported regime state:

- Reported regime: `stagflation`
- Reported probability: 28.76%
- Reported confidence/margin versus raw leader: -1.03%
- Transition filter reason: `held_below_min_confidence`

Interpretation:

The raw signal narrowly favors `reflation`, but the edge over the existing reported state is below the 2% transition threshold. The reported state therefore remains `stagflation`. This is exactly the raw-vs-reported separation introduced in Phase S: raw probabilities show the boundary condition, while reported state avoids switching on a weak signal.

This should be described as a low-confidence boundary state, not a strong `stagflation` or `reflation` call.

## Historical Diagnostics

| Metric | Value |
|---|---:|
| Valid dates | 437 |
| Invalid dates | 0 |
| Reported transition count | 43 |
| Near-zero reported transitions | 0 |
| Low-confidence reported transitions | 8 |
| Average regime duration | 9.93 months |
| Average raw confidence | 19.17% |
| Low-confidence periods | 86 |

Reported regime distribution:

| Regime | Share |
|---|---:|
| goldilocks | 11.44% |
| recession | 25.63% |
| reflation | 27.69% |
| stagflation | 10.07% |
| tightening | 25.17% |

The promoted sources did not create invalid dates, did not reintroduce near-zero reported transitions, and did not produce an unstable timeline.

## Dimension Coverage

Latest dimension coverage:

| Dimension | Valid feature count | Status |
|---|---:|---|
| growth_momentum | 4 | valid |
| inflation_pressure | 2 | valid |
| policy_stance | 3 | valid |
| credit_liquidity | 3 | valid |
| yield_curve | 1 | valid |

Phase U expectation was met:

- Growth breadth improved from 3 to 4 features.
- Credit breadth improved from 2 to 3 features.

## Comparison to Phase U Expectations

Expected:

- `ICSA` usable: yes
- `BAMLH0A0HYM2` usable: yes
- Latest valid date current: yes
- Invalid dates remain zero: yes
- Near-zero reported transitions remain zero: yes
- Raw and reported outputs visible: yes
- `RSAFS` absent: yes
- `T5YIE` absent: yes

The live current label differs from the Phase U experiment because live data/cache state refreshed. That does not change the promotion conclusion: the current state remains low-confidence and boundary-like, while the new sources are operationally healthy and economically interpretable.

## Report Check

`outputs/current_regime.md` shows:

- Reported regime
- Reported probability
- Transition filter reason
- Raw monthly signal
- Full raw probability table
- Data health warnings

The report clearly shows that raw `reflation` is the current monthly leader, while reported `stagflation` is being held by the transition filter.

## Recommendation

Phase V is a production pass.

Next recommended phase:

Phase W: v0.1 release hardening.

Scope should include:

- README cleanup
- `.env.example`
- command examples
- generated-output policy
- known limitations
- reproducibility notes
- clear “what this model is / is not” language

Do not add more sources, trading logic, or allocation logic before hardening the v0.1 release candidate.
