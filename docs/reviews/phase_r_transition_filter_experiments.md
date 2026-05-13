# Phase R Transition-Filter Experiments

Date reviewed: 2026-05-13

Objective: run narrow transition-filter experiments for the `policy_tightening_heavy_v2` formula candidate. Production formulas and production config were not changed.

This phase did not expand sources, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or overwrite production regime outputs.

## Verdict

Phase R passes.

Recommended next step:

- Promote `policy_tightening_heavy_v2` together with a reported-transition filter in a separate Phase S implementation.
- Preferred filter: `min_confidence_to_switch: 0.02`.
- Keep raw monthly regime probabilities unchanged and visible.
- Treat the filtered state as the reported regime timeline, not as a replacement for raw probabilities.
- Do not expand sources until Phase S promotion and validation are complete.

The key model-design result is now clear:

```text
Raw dominance should remain monthly and unsmoothed.
Reported regime transitions should require a small probability gap.
```

## Experiment Setup

Experiment config:

- `config/experiments/phase_r.yaml`

Command:

```powershell
python -m macro_engine.cli run-calibration-experiments --experiment-config config/experiments/phase_r.yaml
```

Outputs:

- `outputs/experiments/phase_r/baseline.json`
- `outputs/experiments/phase_r/policy_tightening_heavy_v2_raw.json`
- `outputs/experiments/phase_r/policy_tightening_heavy_v2_gap_0_01.json`
- `outputs/experiments/phase_r/policy_tightening_heavy_v2_gap_0_02.json`
- `outputs/experiments/phase_r/policy_tightening_heavy_v2_gap_0_03.json`
- `outputs/experiments/phase_r/policy_tightening_heavy_v2_gap_0_02_light_persistence.json`
- `outputs/experiments/phase_r/comparison.json`
- `outputs/experiments/phase_r/comparison.md`

Production regime tables were not overwritten.

## Formula Quality

The formula candidate keeps its Phase P advantages:

| Metric | Production baseline | Candidate raw |
|---|---:|---:|
| Current dominant regime | stagflation | stagflation |
| Current probability | 31.60% | 32.84% |
| Current confidence | 6.36% | 6.61% |
| Average confidence | 17.23% | 19.15% |
| Median confidence | 11.11% | 13.33% |
| Low-confidence periods | 101 | 87 |
| Raw regime switches | 63 | 65 |
| `stagflation` vs `tightening` correlation | 0.673 | 0.440 |
| `recession` vs `stagflation` correlation | 0.517 | 0.517 |

Interpretation:

`policy_tightening_heavy_v2` still improves the main overlap problem without worsening the secondary `recession` vs `stagflation` overlap. The remaining issue is transition noise, not formula direction.

## Filter Results

| Variant | Raw switches | Filtered switches | Near-zero filtered transitions | Low-confidence filtered transitions | 2-month reversal pairs | March 2020 delayed? |
|---|---:|---:|---:|---:|---:|---|
| Production baseline | 63 | 63 | 8 | 21 | 17 | No |
| Candidate raw | 65 | 65 | 13 | 26 | 22 | No |
| Candidate + gap 0.01 | 65 | 54 | 0 | 12 | 14 | No |
| Candidate + gap 0.02 | 65 | 50 | 0 | 5 | 13 | No |
| Candidate + gap 0.03 | 65 | 45 | 0 | 0 | 11 | No |
| Candidate + gap 0.02, light persistence | 65 | 40 | 0 | 1 | 5 | No |

Definitions:

- Near-zero transition: transition confidence below 0.01.
- Low-confidence transition: transition confidence below 0.03.
- Confidence is the top-regime probability minus the second-regime probability.

## Best Filter

The best balance is:

```yaml
transition_filter:
  min_confidence_to_switch: 0.02
```

Why:

- Removes all near-zero reported transitions.
- Reduces low-confidence reported transitions from 26 to 5.
- Reduces 2-month reversal pairs from 22 to 13.
- Keeps 50 reported switches, which is lower than baseline but not collapsed.
- Does not delay the March 2020 crisis transition.
- Keeps the formula candidate's improved `stagflation` vs `tightening` separation.

## Filters Not Recommended

`min_confidence_to_switch: 0.01`

- Good first pass, but leaves 12 low-confidence transitions.
- Better than raw candidate, but not clean enough if a filter is being introduced.

`min_confidence_to_switch: 0.03`

- Very clean transition set, but likely too suppressive for v0.1.
- Switches fall to 45, which is a larger behavioral change than needed.

`min_confidence_to_switch: 0.02` plus light persistence

- Too smooth for this stage.
- Switches fall to 40 and reversal pairs nearly disappear.
- This risks converting a regime diagnostic into a slow-moving label.

## Latest Filtered Transitions

For the preferred `gap_0_02` variant:

| Date | Transition | Confidence |
|---|---|---:|
| 2021-03-01 | stagflation -> reflation | 19.89% |
| 2022-03-01 | reflation -> tightening | 7.18% |
| 2024-09-01 | tightening -> goldilocks | 3.22% |
| 2026-01-01 | goldilocks -> reflation | 6.50% |
| 2026-03-01 | reflation -> stagflation | 6.15% |

This removes the raw candidate's 2025-12 near-tie transition while preserving the 2026-03 transition into `stagflation`.

## Economic Assessment

The preferred package is economically coherent:

- Tightening requires more policy support and growth resilience.
- Stagflation remains the current reported regime, but with modest confidence.
- Boundary transitions with probability ties are suppressed.
- Crisis-like transitions are not materially delayed.
- The model remains a revised-data diagnostic, not a point-in-time vintage backtest.

The filter should be described as a reporting-state rule, not as model scoring. Raw probabilities should still be emitted every month so users can see when the model is uncertain or changing beneath the reported state.

## Recommendation for Phase S

Implement a controlled production update:

1. Promote `policy_tightening_heavy_v2` regime formula changes.
2. Add a production transition-state layer with:

```yaml
transition_filter:
  enabled: true
  min_confidence_to_switch: 0.02
```

3. Preserve raw regime probabilities and raw dominant regime in outputs.
4. Add reported regime state and reported transitions as separate outputs.
5. Rerun live pipeline and reports.
6. Confirm:
   - Latest valid date remains current.
   - Current raw dominant regime and reported regime are explainable.
   - March 2020 transition is not delayed.
   - Source expansion is still deferred until Phase S passes.

No further broad formula experiments are recommended before Phase S.
