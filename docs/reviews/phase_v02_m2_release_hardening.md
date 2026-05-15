# v0.2-M2 Sector Mapper Release Hardening

Date: 2026-05-15

## Verdict

v0.2-M2 passes. v0.2 is release-ready as an experimental sector macro diagnostic layer on top of the v0.1 macro engine.

Release decision: create `v0.2-rc1`.

The sector mapper should not be described as empirically validated or decision-useful by itself. It is a transparent diagnostic layer with weak/mixed ETF proxy validation and clear non-advice guardrails.

## Final Validation Results

- Tests: 127 passed, 2 skipped
- Ruff: passed
- Config validation: passed
- Live macro pipeline: success with warnings
- Sector score build: passed
- Sector report generation: passed
- Sector ETF proxy validation: passed
- Sector validation report generation: passed

Live pipeline summary:

- Status: `success_with_warnings`
- Series requested: 12
- Series succeeded: 12
- Warning count: 2
- Stale series: PCEPI
- Latest valid regime date: 2026-05-01

## Final Macro Output

- Date: 2026-05-01
- Reported regime: reflation
- Raw dominant regime: reflation
- Reported probability: 39.57%
- Raw confidence: 15.63%
- Transition filter reason: `raw_signal_confirmed`

Raw regime probabilities:

| Regime | Probability |
| --- | ---: |
| reflation | 39.57% |
| tightening | 23.94% |
| stagflation | 18.39% |
| goldilocks | 12.43% |
| recession | 5.66% |

## Final Sector Ranking

| Rank | Sector | Raw Score | Confidence-Adjusted Score |
| ---: | --- | ---: | ---: |
| 1 | energy | 1.3243 | 0.6538 |
| 2 | materials | 0.9859 | 0.4868 |
| 3 | financials | 0.7446 | 0.3676 |
| 4 | industrials | 0.6714 | 0.3315 |
| 5 | consumer_staples | -0.0593 | -0.0293 |
| 6 | health_care | -0.2196 | -0.1084 |
| 7 | consumer_discretionary | -0.2408 | -0.1189 |
| 8 | communication_services | -0.2996 | -0.1479 |
| 9 | information_technology | -0.4698 | -0.2319 |
| 10 | real_estate | -0.8786 | -0.4338 |
| 11 | utilities | -0.9453 | -0.4667 |

This ranking is a macro diagnostic ranking only. It is not a recommendation to buy, sell, avoid, overweight, underweight, allocate to, or trade any sector or ETF.

## Sector Validation Summary

| Horizon | Observations | Rank IC | Top-Bottom Spread | Top Positive Hit Rate |
| --- | ---: | ---: | ---: | ---: |
| 1m | 836 | 0.0044 | 0.0025 | 0.5197 |
| 3m | 814 | 0.0249 | 0.0123 | 0.5405 |

The validation remains diagnostic only. The latest run is directionally positive, but rank IC values are small and the local ETF proxy data should be treated as validation input rather than a claim of tradable signal.

## Source Health Summary

- Fresh usable sources: 11
- Stale but usable sources: 1
- Disabled/unusable health-test source: 1

Notable warning:

- `PCEPI` is stale but still usable under current health rules.

Disabled:

- `USSLIND` remains disabled as discontinued/stale.

Production source exclusions remain correct:

- `RSAFS` is not promoted.
- `T5YIE` is not promoted.

## Documentation Updates

Updated:

- `README.md`
- `docs/model_limitations.md`

Added:

- `docs/release_checklist_v0_2.md`

The docs now state:

- sector scores are diagnostics only
- sector assumptions are heuristic
- ETF proxy validation is diagnostic only
- proxy tickers are validation/reporting references only
- no transaction costs, slippage, execution constraints, allocation sizing, or strategy are modeled
- v0.2 sector validation is weak/mixed and the sector layer remains experimental

## Artifact Hygiene

Confirmed:

- `.env` is ignored and not staged
- `data/` is ignored and not staged
- `outputs/` is ignored and not staged
- generated experiment outputs are ignored and not staged

## Language Guardrails

Generated sector reports were checked for forbidden recommendation language. No buy, sell, overweight, underweight, or avoid wording appeared.

The sector reports preserve diagnostic language and non-advice disclaimers.

## Release Blockers

None for an experimental v0.2 release candidate.

## Non-Blocking Follow-Ups

- Re-run sector ETF proxy validation with verified real adjusted-close ETF data.
- Revisit real-estate and technology calibration variants only if real-data validation confirms the same directional improvement.
- Consider an optional sector flag in `run-pipeline` later, but keep sector scoring as an explicit post-macro step for now.
- Do not start news ingestion until the v0.2 release candidate is accepted.

