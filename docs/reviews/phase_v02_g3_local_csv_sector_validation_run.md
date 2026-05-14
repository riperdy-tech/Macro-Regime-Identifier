# v0.2-G3 Local CSV Sector Validation Run

Date: 2026-05-15

## Verdict

v0.2-G3 passes as an empirical validation run. The local ETF proxy CSV unblocked validation, produced usable price rows, and generated sector validation outputs.

The validation result is mixed rather than strong. Rank IC is slightly positive at both horizons, but the 1-month top-minus-bottom spread is negative and the 3-month spread is positive but small. This does not justify treating the current sector exposure assumptions as fully validated.

Recommendation: do not add news and do not add trading or allocation logic. Before v0.2 release hardening, run a focused sector-assumption calibration review.

## Data

- CSV path: `data/sector_proxy_prices.csv`
- Source file observed locally: `data/sector_proxy_prices_sample.csv`
- CSV status: local-only generated/input data under ignored `data/`
- Required schema: `ticker,date,close`
- Price rows loaded: 19,932
- Price date range: 2020-01-02 to 2026-05-14
- Tickers loaded: SPY, XLB, XLC, XLE, XLF, XLI, XLK, XLP, XLRE, XLU, XLV, XLY
- Missing required tickers: none
- Rows per ticker: 1,661

## Validation Outputs

- Return rows: 4,807
- Valid return rows: 836
- Invalid return rows: 3,971
- Invalid reason: `missing_forward_prices`
- Validation score date range: 2020-01-01 to 2026-04-01
- Output files:
  - `outputs/sector_validation.json`
  - `outputs/sector_validation.md`

## Metrics

| Horizon | Observations | Rank IC | Top Avg Relative Return | Bottom Avg Relative Return | Top Minus Bottom | Top Positive Hit Rate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1m | 836 | 0.0122 | 0.0002 | 0.0029 | -0.0027 | 0.5066 |
| 3m | 814 | 0.0439 | 0.0068 | 0.0049 | 0.0019 | 0.5541 |

## Interpretation

The 1-month validation does not support a clean near-term sector ranking signal. Rank IC is barely positive, but top-ranked sectors underperformed bottom-ranked sectors on average.

The 3-month validation is directionally better. Rank IC is modestly positive, the top-minus-bottom spread is positive, and the top positive hit rate is above 50%. The magnitude is still small, so this is weak diagnostic support rather than strong validation.

The sector-level averages also suggest possible assumption issues. Sectors with more negative average scores, including consumer discretionary, information technology, financials, and industrials, had positive average relative returns in the available sample. Higher-scored energy and consumer staples did not show clear relative strength in the same window. This may reflect sample-period effects, macro-score timing, sector assumption weights, or the use of unadjusted close data.

## Implementation Bug Fixed

During validation, a date-alignment bug was found and fixed. The validator previously allowed score dates before the ETF price history to use the first available future ETF price as the start price. That incorrectly marked pre-2020 score dates as valid even though the CSV begins in 2020.

The fix allows normal next-trading-day alignment but rejects prices more than seven calendar days after the target date. A regression test now verifies that far-future prices are not used for old score dates. The Spearman helper also now skips constant rank slices, avoiding noisy degenerate correlation warnings.

## Language Guardrails

The generated validation report states that this is a diagnostic validation, not a trading backtest. It also states that ETF tickers are validation references only, and that no transaction costs, slippage, execution constraints, or allocation sizing are modeled.

No buy, sell, overweight, underweight, avoid, allocation, or trading recommendation language was introduced.

## Validation Commands

- `python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml`
- `python -m macro_engine.cli build-sector-scores --config config/phase_b_sources.yaml`
- `python -m macro_engine.cli current-sector-ranking`
- `python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml`
- `python -m macro_engine.cli sector-validation-summary`
- `python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml`
- `python -m pytest`
- `python -m ruff check .`
- `python -m macro_engine.cli validate-config`

## Validation Results

- Tests: 124 passed, 2 skipped
- Ruff: passed
- Config validation: passed
- Sector price ingestion: passed
- Sector validation: passed
- Sector validation report generation: passed

## Next Step

Proceed to a focused v0.2 sector-assumption calibration review before v0.2 release hardening. The review should examine whether the current regime priors and dimension exposure weights are too defensive, too inflation-sensitive, or insufficiently responsive to growth and policy effects during the 2020-2026 validation window.

