# Phase Q Transition Review

Date reviewed: 2026-05-13

Objective: review the Phase P best candidate, `policy_tightening_heavy_v2`, with a narrow focus on transition quality. Production formulas were not changed.

This phase did not expand sources, add trading logic, add allocation logic, implement ALFRED/vintage backtesting, or change production config. The review used stored Phase P experiment definitions and current calendar-aligned dimension scores.

## Verdict

Phase Q passes as a review phase, but `policy_tightening_heavy_v2` should not be promoted raw.

Recommendation:

- Keep production formulas unchanged for now.
- Keep `softmax_temperature: 0.6`.
- Do not expand sources yet.
- Run one narrow transition-filter experiment phase before any formula promotion.

The candidate is still the best formula direction, because it improves `stagflation` vs `tightening` separation without worsening `recession` vs `stagflation`. The transition review shows the drawback more clearly: the extra transitions are concentrated in low-confidence or fast-reversal areas, especially a 1995 `tightening`/`recession` whipsaw cluster.

## Baseline vs Candidate

| Metric | Production baseline | policy_tightening_heavy_v2 |
|---|---:|---:|
| Valid dates | 437 | 437 |
| Regime switches | 63 | 65 |
| Average duration | 6.83 months | 6.62 months |
| Average confidence | 17.23% | 19.15% |
| Near-zero transitions, confidence < 0.01 | 8 | 13 |
| Low-confidence transitions, confidence < 0.03 | 21 | 26 |
| One-month reversal pairs | 13 | 18 |
| One-to-two-month reversal pairs | 17 | 22 |
| `stagflation` vs `tightening` raw-score correlation | 0.673 | 0.440 |
| `recession` vs `stagflation` raw-score correlation | 0.517 | 0.517 |

Interpretation:

The candidate improves the main formula-overlap problem, but it also increases noisy transition behavior. The higher average confidence is real, but the additional low-confidence transitions argue against immediate production promotion.

## Latest Transitions

Latest 10 transitions for `policy_tightening_heavy_v2`:

| Date | Transition | From probability | To probability | Confidence |
|---|---|---:|---:|---:|
| 2019-08-01 | stagflation -> recession | 33.69% | 42.03% | 14.94% |
| 2020-02-01 | recession -> stagflation | 35.36% | 27.78% | 2.35% |
| 2020-03-01 | stagflation -> recession | 27.78% | 91.53% | 83.37% |
| 2020-12-01 | recession -> reflation | 31.16% | 26.73% | 0.90% |
| 2021-02-01 | reflation -> stagflation | 30.60% | 28.56% | 2.28% |
| 2021-03-01 | stagflation -> reflation | 28.56% | 43.76% | 19.89% |
| 2022-03-01 | reflation -> tightening | 42.28% | 35.50% | 7.18% |
| 2024-09-01 | tightening -> goldilocks | 26.91% | 25.57% | 3.22% |
| 2025-12-01 | goldilocks -> reflation | 25.88% | 22.53% | 0.73% |
| 2026-03-01 | reflation -> stagflation | 30.95% | 32.07% | 6.15% |

The latest candidate transition into `stagflation` is not a near-tie, but the 2025-12 transition is. This reinforces the Phase M/Phase P interpretation: current `stagflation` is plausible, but not a strong macro call.

## Candidate-Only Transitions

Transitions present in the candidate but not in the baseline:

| Date | Transition | Confidence | Assessment |
|---|---|---:|---|
| 1995-08-01 | recession -> tightening | 0.50% | Noise-like; part of a rapid reversal cluster |
| 1995-09-01 | tightening -> recession | 0.91% | Noise-like; immediate reversal |
| 1995-10-01 | recession -> tightening | 0.54% | Noise-like; immediate reversal |
| 1995-11-01 | tightening -> recession | 3.86% | Ends the whipsaw cluster |
| 1996-10-01 | tightening -> reflation | 2.29% | Low confidence |
| 2000-06-01 | tightening -> stagflation | 0.30% | Noise-like |
| 2007-02-01 | tightening -> goldilocks | 0.01% | Noise-like; reverses next month |
| 2016-12-01 | stagflation -> tightening | 1.80% | Low confidence |
| 2017-10-01 | stagflation -> tightening | 0.19% | Noise-like; follows prior month tightening -> stagflation |

This is the main reason not to promote the candidate without a transition rule. The candidate-only transitions are mostly not strong regime shifts; they are probability ties around a boundary.

## Fast Reversals

`policy_tightening_heavy_v2` has 22 reversal pairs within one to two months, versus 17 for production baseline.

Most important candidate reversal clusters:

- 1995-07 through 1995-11: repeated `tightening` <-> `recession` switches with confidence mostly below 1%.
- 2007-02 to 2007-03: `tightening` -> `goldilocks` -> `tightening`, first leg confidence 0.01%.
- 2017-09 to 2017-10: `tightening` -> `stagflation` -> `tightening`, second leg confidence 0.19%.
- 2020-02 to 2020-03: `recession` -> `stagflation` -> `recession`; the March 2020 reversal is economically plausible and high confidence, but the February transition is low confidence.

Conclusion:

Some reversals are economically plausible around real turning points, especially 2020. The extra candidate-only reversals are mostly not economically strong enough to justify raw production promotion.

## Review-Only Transition Filter Simulation

These filter checks were review-only. They did not write production outputs and did not change the model.

| Filter | Switches | Low-confidence transitions < 0.03 | Reversal pairs within 2 months | Assessment |
|---|---:|---:|---:|---|
| Candidate raw | 65 | 26 | 22 | Better formula separation, too much boundary noise |
| Gap >= 0.01, no confirmation | 54 | 12 | 14 | Reduces noise but still leaves reversals |
| Gap >= 0.02, no confirmation | 50 | 5 | 13 | Cleaner, but transition history changes materially |
| Gap >= 0.03, no confirmation | 45 | 0 | 11 | Removes low-confidence switches, likely too suppressive |
| Gap >= 0.03, 2-month confirmation | 32 | 0 | 0 | Too smooth for v0.1; likely delays real turns |

`confirmation_months: 2` is probably too blunt for this engine. It removes whipsaws, but it also cuts total transitions roughly in half and delays several meaningful transitions by one month. A simple minimum confidence gap may be more appropriate, but even `0.03` lowers the switch count from 65 to 45, which is a material change that needs its own experiment.

## Economic Assessment

The candidate formula is economically better than production baseline in one important way:

- Tightening becomes more policy-dependent and less tolerant of weak growth.

That directly addresses the original `stagflation` vs `tightening` overlap.

But the transition path is not yet production-worthy:

- Extra transitions are mostly low-confidence.
- Candidate-only transitions include several one-month reversals.
- The 1995 cluster is especially noise-like.
- A transition filter appears useful, but the first simple filters either leave some whipsaws or materially suppress transition history.

There is no implementation bug indicated by this review. This is model-state logic: raw monthly dominance changes should probably be separated from reported regime transitions.

## Recommendation for Phase R

Run a narrow transition-filter experiment phase.

Recommended variants:

- Production baseline, no filter.
- `policy_tightening_heavy_v2`, no filter.
- `policy_tightening_heavy_v2` with `min_confidence_to_switch: 0.01`.
- `policy_tightening_heavy_v2` with `min_confidence_to_switch: 0.02`.
- `policy_tightening_heavy_v2` with `min_confidence_to_switch: 0.03`.
- Optional: `min_confidence_to_switch: 0.02` plus a light persistence rule that only requires two months when the switch confidence is below 0.05.

Success criteria:

- Preserve the candidate's improved `stagflation` vs `tightening` separation.
- Keep `recession` vs `stagflation` no worse than baseline.
- Reduce near-zero transitions materially.
- Avoid cutting total switch count below a plausible historical range.
- Avoid delaying obvious crisis transitions such as March 2020.

Promotion decision:

Do not promote `policy_tightening_heavy_v2` raw. Promote it only if Phase R finds a transition rule that removes boundary noise without over-smoothing historical regime changes.

Source expansion should still wait until transition behavior is settled.
