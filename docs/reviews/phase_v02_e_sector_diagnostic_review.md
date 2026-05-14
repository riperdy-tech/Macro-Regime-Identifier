# v0.2-E Sector Diagnostic Review

Date: 2026-05-14

## Verdict

v0.2-E passes as a sector macro mapper diagnostic review.

The sector layer is explainable from stored macro outputs and component rows. No macro regime formulas, sector scoring formulas, source sets, trading logic, allocation logic, ETF validation, return backtesting, or news ingestion were added.

Recommendation: proceed to v0.2-F sector ETF proxy validation as a diagnostic validation phase. Do not change sector assumptions yet; use v0.2-F to test whether the current heuristic mapper has historical relative-performance signal before tuning exposures or priors.

## Validation Results

Commands run:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli run-pipeline --config config/phase_b_sources.yaml
python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml
python -m macro_engine.cli current-sector-ranking
python -m macro_engine.cli write-sector-report --config config/phase_b_sources.yaml
```

Results:

```text
pytest: 113 passed, 2 skipped
ruff: all checks passed
validate-config: Config valid: 13 sources, 11 dimensions, 6 regimes
run-pipeline: success_with_warnings
sector scores: 4807 valid rows out of 4807
sector components: 48070 rows
sector health: 4807 rows
```

Live macro pipeline warnings were explainable:

```text
stale_series: INDPRO, PCEPI
series_requested: 12
series_succeeded: 12
latest_valid_regime_date: 2026-05-01
```

## Current Macro Context

Latest macro date:

```text
2026-05-01
```

Macro state consumed by the sector mapper:

```text
reported_macro_regime: reflation
raw_macro_leader: reflation
macro_confidence: 0.1316
```

The sector mapper correctly used stored macro outputs rather than recalculating regime probabilities.

## Current Sector Ranking

Full sector ranking:

```text
1.  energy                    raw  1.084   adjusted  0.519
2.  materials                 raw  0.573   adjusted  0.274
3.  industrials               raw  0.168   adjusted  0.080
4.  financials                raw  0.164   adjusted  0.079
5.  consumer_staples          raw  0.062   adjusted  0.030
6.  health_care               raw -0.167   adjusted -0.080
7.  communication_services    raw -0.513   adjusted -0.246
8.  consumer_discretionary    raw -0.700   adjusted -0.335
9.  utilities                 raw -0.739   adjusted -0.354
10. information_technology    raw -0.808   adjusted -0.387
11. real_estate               raw -1.054   adjusted -0.505
```

Top 3:

```text
energy
materials
industrials
```

Bottom 3:

```text
utilities
information_technology
real_estate
```

## Top-Sector Explanation

Energy ranked first. Its score is explainable and mainly inflation/reflation driven:

```text
inflation_pressure exposure: +0.972
reflation prior: +0.109
stagflation prior: +0.081
yield_curve exposure: +0.075
credit_liquidity exposure: -0.158
growth_momentum exposure: -0.018
```

Materials ranked second:

```text
inflation_pressure exposure: +0.486
yield_curve exposure: +0.150
reflation prior: +0.127
credit_liquidity exposure: -0.198
growth_momentum exposure: -0.033
```

Industrials ranked third:

```text
yield_curve exposure: +0.188
inflation_pressure exposure: +0.139
reflation prior: +0.127
credit_liquidity exposure: -0.237
growth_momentum exposure: -0.039
```

Interpretation: top-sector support is mostly coming from reflation/inflation-sensitive assumptions, partly offset by weak growth and tighter credit/liquidity conditions.

## Bottom-Sector Explanation

Real Estate ranked last:

```text
credit_liquidity exposure: -0.435
inflation_pressure exposure: -0.347
yield_curve exposure: -0.225
tightening prior: -0.089
policy_stance exposure: +0.136
```

Information Technology ranked tenth:

```text
inflation_pressure exposure: -0.417
credit_liquidity exposure: -0.237
yield_curve exposure: -0.150
stagflation prior: -0.069
policy_stance exposure: +0.119
```

Utilities ranked ninth:

```text
yield_curve exposure: -0.300
inflation_pressure exposure: -0.278
credit_liquidity exposure: -0.158
reflation prior: -0.091
policy_stance exposure: +0.119
```

Interpretation: bottom-sector pressure is also explainable from configured assumptions. Real estate is most affected by credit, inflation, and yield-curve exposures; technology is mainly penalized by inflation and credit sensitivity; utilities are pressured by yield-curve, inflation, and reflation prior assumptions.

## Confidence Adjustment

The confidence adjustment is working.

Macro confidence was:

```text
0.1316
```

Configured multiplier:

```text
0.40 + (0.60 * 0.1316) = 0.4790
```

Examples:

```text
energy raw 1.084 -> adjusted 0.519
materials raw 0.573 -> adjusted 0.274
real_estate raw -1.054 -> adjusted -0.505
```

This prevents the sector report from presenting overly decisive rankings when macro confidence is still modest.

## Stability And Compression Review

The ranking is not excessively compressed at the top and bottom:

```text
adjusted top score:  0.519
adjusted bottom score: -0.505
adjusted range:       1.024
```

The middle of the ranking is compressed:

```text
industrials:       0.080
financials:        0.079
consumer_staples:  0.030
health_care:      -0.080
```

This is not a bug. It reflects offsetting component contributions and modest macro confidence. The middle ranks should be interpreted cautiously.

## Component Dominance Review

Some sectors are heavily influenced by one large macro dimension contribution:

```text
energy: inflation_pressure is the largest support
materials: inflation_pressure is the largest support
real_estate: credit_liquidity is the largest pressure
information_technology: inflation_pressure is the largest pressure
utilities: yield_curve is the largest pressure
```

This is acceptable for v0.2-E because these are transparent assumptions and all component rows are inspectable. It should be monitored in v0.2-F. A validation phase should test whether these large single-dimension sensitivities create useful or misleading historical sector diagnostics.

## Report Language Review

Reviewed:

```text
outputs/current_sector_ranking.json
outputs/current_sector_ranking.md
```

The report uses diagnostic language:

```text
positive macro tailwind score
negative macro sensitivity score
weak diagnostic signal
not investment advice
no trading, allocation, portfolio sizing, or security selection guidance
proxy tickers are reporting references only
```

The generated Markdown did not contain recommendation language such as buy, sell, overweight, underweight, or avoid.

## Release Blockers

None for v0.2-E.

## Non-Blocking Follow-Ups

1. v0.2-F should compare sector macro scores against sector ETF proxy relative returns as a diagnostic validation exercise, not a trading backtest.
2. The middle sector ranks are close; later reports may benefit from grouping sectors into positive, neutral, and negative diagnostic bands.
3. Component dominance should be reviewed after ETF proxy validation before changing exposures or priors.
4. Sector scoring remains optional post-pipeline output; do not force it into `run-pipeline` until a deliberate `--include-sectors` design is approved.

## v0.2-F Recommendation

Proceed to v0.2-F sector ETF proxy validation.

The current sector mapper is sufficiently explainable and traceable to justify validation. It is too early to tune sector assumptions, because there is not yet evidence that the current heuristic exposure matrix fails historical sanity checks.
