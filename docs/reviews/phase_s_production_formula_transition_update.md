# Phase S Production Formula and Transition Update

Date reviewed: 2026-05-13

Objective: promote the Phase R v0.1 candidate package into production:

- `policy_tightening_heavy_v2` tightening formula
- reported-transition filter with `min_confidence_to_switch: 0.02`

This phase did not expand sources, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or add new formula experiments.

## Verdict

Phase S passes as a controlled production promotion.

The model now separates two concepts:

```text
Raw monthly signal = unsmoothed regime probabilities and raw dominant regime.
Reported regime state = transition-filtered state used for timeline/transitions/reports.
```

That preserves monthly uncertainty while suppressing low-confidence whipsaws in the reported regime timeline.

## Production Changes

Changed:

- Promoted `policy_tightening_heavy_v2` tightening formula.
- Added production transition filter:

```yaml
transition_filter:
  enabled: true
  min_confidence_to_switch: 0.02
```

Unchanged:

- Source universe
- Feature transforms
- Feature normalizations
- Dimension weights
- Softmax temperature, still `0.6`
- Non-tightening regime formulas
- Trading/allocation logic
- ALFRED/vintage backtesting

## Live Pipeline Result

Command:

```powershell
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
```

Pipeline result:

- Status: `success_with_warnings`
- Series requested: 10
- Series succeeded: 10
- Stale series: `INDPRO`, `PCEPI`
- Latest valid regime date: 2026-05-01
- Output reports:
  - `outputs/current_regime.json`
  - `outputs/current_regime.md`
  - `outputs/historical_diagnostic.json`
  - `outputs/historical_diagnostic.md`

The stale source warnings should be monitored, but they did not prevent full regime coverage for the latest date.

## Current Regime

Latest valid date: 2026-05-01

Raw monthly signal:

- Raw dominant regime: `stagflation`
- Raw dominant probability: 32.84%
- Raw confidence: 6.61%

Reported regime state:

- Reported regime: `stagflation`
- Reported probability: 32.84%
- Reported confidence: 6.61%
- Transition filter applied: true
- Transition filter reason: `raw_signal_confirmed`

Raw probabilities:

| Regime | Probability |
|---|---:|
| stagflation | 32.84% |
| reflation | 26.23% |
| tightening | 21.86% |
| recession | 10.82% |
| goldilocks | 8.24% |

Interpretation:

The current regime remains `stagflation`, but this is still a moderate-confidence output, not a strong macro call.

## Historical Diagnostic

Mode: `revised_data`

| Metric | Value |
|---|---:|
| Valid dates | 437 |
| Invalid dates | 0 |
| Reported transition count | 50 |
| Average reported regime duration | 8.57 months |
| Average raw confidence | 19.15% |
| Low-confidence periods | 87 |
| Near-zero reported transitions | 0 |
| Low-confidence reported transitions | 5 |

Reported dominant regime distribution:

| Regime | Share |
|---|---:|
| goldilocks | 11.21% |
| recession | 25.17% |
| reflation | 28.38% |
| stagflation | 11.67% |
| tightening | 23.57% |

Latest reported transitions:

| Date | Transition |
|---|---|
| 2021-03-01 | stagflation -> reflation |
| 2022-03-01 | reflation -> tightening |
| 2024-09-01 | tightening -> goldilocks |
| 2026-01-01 | goldilocks -> reflation |
| 2026-03-01 | reflation -> stagflation |

## Transition Filter Check

The reported-transition filter performed as expected:

- Near-zero reported transitions were removed: `13 -> 0` versus the raw candidate.
- Low-confidence reported transitions fell: `26 -> 5`.
- Reported switch count is 50, which is below the raw candidate's 65 but not collapsed.
- March 2020 recession transition remains on 2020-03-01.

This confirms the filter is acting as a reporting-state rule rather than changing scoring.

## Report Check

`outputs/current_regime.md` now includes:

- Reported regime
- Reported probability
- Reported confidence
- Transition filter reason
- Raw monthly dominant regime
- Raw monthly probability
- Raw monthly confidence
- Full raw probability table

The report still explains the dominant reported regime using stored contribution rows. No report-side scoring recomputation was introduced.

## Implementation Notes

The production scoring tables still store raw regime probabilities. The historical diagnostic timeline now stores both:

- `raw_dominant_regime`
- `raw_dominant_probability`
- `raw_confidence`
- `reported_regime`
- `reported_regime_probability`
- `reported_confidence`
- `transition_filter_applied`
- `transition_filter_reason`

Backward-compatible `dominant_regime` and `dominant_probability` fields remain available and represent the reported timeline state. The existing `confidence` field remains the raw top-versus-second probability gap used to validate transitions.

## Recommendation

Proceed next to Phase T: controlled source expansion, but keep it narrow.

Suggested Phase T scope:

- Add only a small number of high-value sources.
- Keep the new formula and transition filter fixed while testing source additions.
- Continue requiring source-health checks, calendar alignment, full pipeline validation, and human-readable review.

Do not add trading or allocation logic yet.
