# v0.2-M1 Sector Calibration Package

Date: 2026-05-15

## Verdict

v0.2-M1 passes as a bundled sector calibration package.

Decision: do not promote a calibrated sector assumption variant yet. Keep production sector assumptions unchanged and proceed to v0.2-M2 release hardening with the sector mapper labeled as an experimental diagnostic layer.

Reason: the best variants modestly improve 3-month validation, but the validation data is a synthetic/sample local ETF proxy CSV, the 1-month signal remains weak, and the improvement is not strong enough to justify production assumption changes.

## Scope Guardrails

No macro scoring formulas were changed. No FRED macro sources were added. No news ingestion, trading logic, allocation logic, portfolio sizing, ETF recommendations, security recommendations, or ALFRED/vintage backtesting was introduced.

ETF tickers remain validation proxies only.

## Baseline Refresh

Baseline commands completed:

- `python -m pytest`
- `python -m ruff check .`
- `python -m macro_engine.cli validate-config`
- `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- `python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml`
- `python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml`
- `python -m macro_engine.cli sector-validation-summary`
- `python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml`

Baseline validation summary:

| Horizon | Observations | Rank IC | Top Avg Relative Return | Bottom Avg Relative Return | Top Minus Bottom | Top Positive Hit Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 836 | 0.0122 | 0.0002 | 0.0029 | -0.0027 | 0.5066 |
| 3m | 814 | 0.0439 | 0.0068 | 0.0049 | 0.0019 | 0.5541 |

The 3-month horizon remains more promising than 1 month, but the signal is still small.

## Experiment Package

Added experiment config:

- `config/experiments/sector_calibration_v02_m1.yaml`

Added calibration runner:

- `python -m macro_engine.cli run-sector-calibration-experiments --experiment-config config/experiments/sector_calibration_v02_m1.yaml`

Experiment outputs:

- `outputs/experiments/v02_m1_sector_calibration/baseline_current_assumptions.json`
- one JSON file per variant
- `outputs/experiments/v02_m1_sector_calibration/comparison.json`
- `outputs/experiments/v02_m1_sector_calibration/comparison.md`

Production sector configs were not mutated by the experiment runner.

## Variant Comparison

Primary horizon: 3 months.

| Variant | 3m Rank IC | 3m Top-Bottom | 1m Rank IC | 1m Top-Bottom | Avg Component Dominance |
| --- | ---: | ---: | ---: | ---: | ---: |
| real_estate_rate_credit_rebalance | 0.0550 | 0.0054 | 0.0208 | -0.0029 | 0.3797 |
| technology_rate_inflation_rebalance | 0.0538 | 0.0091 | 0.0136 | -0.0029 | 0.3847 |
| energy_inflation_cap | 0.0458 | -0.0003 | 0.0086 | -0.0017 | 0.3762 |
| baseline_current_assumptions | 0.0439 | 0.0019 | 0.0122 | -0.0027 | 0.3826 |
| higher_dimension_exposure_weight | 0.0410 | 0.0021 | 0.0091 | -0.0029 | 0.3934 |
| inflation_exposure_reduced | 0.0407 | -0.0067 | 0.0056 | -0.0080 | 0.3785 |
| combined_candidate_1 | 0.0356 | -0.0018 | -0.0020 | -0.0082 | 0.4098 |
| defensive_sector_rebalance | 0.0328 | -0.0011 | 0.0037 | -0.0065 | 0.3792 |
| combined_candidate_2 | 0.0307 | 0.0014 | -0.0000 | -0.0063 | 0.4461 |
| lower_regime_prior_weight | 0.0246 | -0.0013 | 0.0044 | -0.0038 | 0.4115 |
| dimension_only_no_regime_prior | 0.0160 | 0.0027 | -0.0013 | -0.0046 | 0.4472 |

No variant created an always-top or always-bottom sector in the experiment output.

## Findings

The best-ranked variant was `real_estate_rate_credit_rebalance`. It improved 3-month rank IC from 0.0439 to 0.0550 and 3-month top-minus-bottom spread from 0.0019 to 0.0054. Its 1-month top-minus-bottom spread remained negative.

The strongest top-minus-bottom improvement came from `technology_rate_inflation_rebalance`, with a 3-month spread of 0.0091. Its 3-month rank IC was also better than baseline at 0.0538. However, its 1-month spread was still negative.

Dimension-only and lower-regime-prior variants did not improve validation. In this experiment, regime priors should not be removed wholesale.

Broad inflation reduction did not help. `inflation_exposure_reduced` worsened both 1-month and 3-month top-minus-bottom spreads.

Combined candidates did not improve on their simpler component variants. `combined_candidate_2` also increased component dominance to 0.4461, which is less attractive.

## Calibration Decision

Do not promote any variant.

The promotion bar was not met because:

- 3-month improvement was modest.
- 1-month metrics remained weak or deteriorated.
- The input validation CSV is synthetic/sample data, so improvements may be artifacts.
- Combined candidates were not better than narrower variants.
- The current production assumptions are explainable and structurally stable.

## Recommended M2 Release Position

Proceed to v0.2-M2 release hardening with the sector mapper explicitly labeled as experimental/diagnostic.

The v0.2 documentation should say:

- Sector scores are heuristic macro diagnostics.
- ETF proxy validation is diagnostic only, not a trading backtest.
- Current validation is weak/mixed and depends on local proxy price data quality.
- The sector layer should not be used as investment advice, trading guidance, allocation guidance, portfolio sizing guidance, or security recommendations.

## Follow-Ups

Non-blocking follow-ups:

- Repeat validation with verified real adjusted-close ETF data.
- Re-test real-estate and technology rebalances on real data.
- Consider a narrow calibration package only if real-data validation confirms the same variant ranking.
- Keep news ingestion out of scope until the sector mapper release candidate is documented and stable.

## Final Validation

- Tests: 127 passed, 2 skipped
- Ruff: passed
- Config validation: passed

