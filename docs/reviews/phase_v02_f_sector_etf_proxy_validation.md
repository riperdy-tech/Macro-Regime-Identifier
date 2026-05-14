# v0.2-F Sector ETF Proxy Validation

Date: 2026-05-14

## Verdict

v0.2-F passes as an implementation of sector ETF proxy validation infrastructure.

The empirical live validation is inconclusive in this environment because the configured Stooq provider returned no usable price rows without a Stooq API key. The validation layer is still ready for use with either:

```text
1. a local CSV containing ticker,date,close proxy prices, or
2. STOOQ_API_KEY set for Stooq CSV downloads.
```

Do not tune sector exposures or priors yet. There is not enough live validation evidence to justify changing assumptions.

## Scope Guardrails

No macro scoring formulas were changed.

No sector scoring assumptions were changed.

No news ingestion, trading logic, allocation logic, portfolio sizing, security recommendations, or ALFRED/vintage backtesting were added.

ETF tickers are validation proxies only.

## Validation Commands

Commands run:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml
python -m macro_engine.cli sector-validation-summary
python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml
```

Results before final full-suite rerun:

```text
new v0.2-F tests: 5 passed
ruff: all checks passed
validate-config: Config valid: 13 sources, 11 dimensions, 6 regimes
price rows ingested from live provider: 0
validation return rows: 4807
valid validation return rows: 0
summary rows: 2
```

## Data Availability

Configured validation proxies:

```text
XLC communication_services
XLY consumer_discretionary
XLP consumer_staples
XLE energy
XLF financials
XLV health_care
XLI industrials
XLK information_technology
XLB materials
XLRE real_estate
XLU utilities
SPY benchmark
```

Configured provider:

```text
provider: stooq
api_key_env: STOOQ_API_KEY
fallback-compatible provider: csv
```

Observed provider result:

```text
price_rows: 0
tickers: []
```

The Stooq endpoint responded with API-key instructions rather than downloadable CSV data. The provider now handles that cleanly by returning zero rows instead of crashing. This is a data availability issue, not a sector validation logic failure.

## Live Validation Metrics

Because no live proxy prices were available, live validation metrics are not available:

```text
date range: n/a
valid observations: 0
1m rank IC: n/a
3m rank IC: n/a
1m top-minus-bottom spread: n/a
3m top-minus-bottom spread: n/a
hit rate top positive: n/a
```

The generated report clearly shows no valid observations and keeps diagnostic, non-backtest language.

## Mocked Validation Coverage

Deterministic tests validate the full workflow using toy sector scores and mocked proxy prices.

Covered behavior:

```text
forward return calculation
relative return calculation versus SPY
missing price handling
rank correlation calculation
validation summary calculation
report generation
CLI ingest/run/summary/report behavior
```

The toy validation case intentionally gives higher sector scores better future relative returns. It confirms:

```text
1m rank IC: 1.0
top-minus-bottom spread: positive
top-sector hit rate: positive
```

That result only validates the machinery. It is not evidence that the real sector mapper has predictive signal.

## Implementation Summary

Added:

```text
config/sector_validation.yaml
sector_proxy_prices table
sector_validation_returns table
sector_validation_summary table
ingest-sector-proxy-prices CLI
run-sector-validation CLI
sector-validation-summary CLI
write-sector-validation-report CLI
outputs/sector_validation.json
outputs/sector_validation.md
```

The validation layer compares stored sector scores at date `t` with future sector ETF proxy returns relative to SPY:

```text
relative_forward_return =
  sector_proxy_forward_return - SPY_forward_return
```

Supported horizons:

```text
1 month
3 months
```

The implementation remains pluggable:

```text
csv provider: deterministic local files
stooq provider: optional live CSV source when STOOQ_API_KEY is available
```

## Report Language Review

The generated validation report states:

```text
diagnostic validation
not a trading backtest
not investment advice
not allocation guidance
not portfolio sizing guidance
not a security recommendation
proxy tickers are validation references only
no transaction costs, slippage, execution constraints, or allocation sizing are modeled
```

No trading or recommendation language was introduced.

## Sector Assumption Review

There is no live evidence yet that positive sector scores corresponded to better future sector-relative returns, because live proxy prices were unavailable.

Therefore:

```text
do not tune sector exposures
do not tune regime-sector priors
do not add news yet
do not treat sector scores as validated
```

The right next step is to run the same validation with usable proxy prices, either from a local CSV or with `STOOQ_API_KEY`.

## Recommendation

Keep sector assumptions unchanged.

Before v0.2 release hardening, provide usable ETF proxy prices and rerun:

```text
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml
python -m macro_engine.cli sector-validation-summary
python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml
```

If live validation then shows a reasonable positive relationship between sector scores and future relative returns, move to v0.2 release hardening. If not, run a targeted sector exposure/prior calibration phase before adding news.
