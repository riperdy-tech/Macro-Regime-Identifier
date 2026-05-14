# v0.2-G2 Local CSV Sector Validation

Date: 2026-05-14

## Verdict

v0.2-G2 is blocked by missing local ETF proxy price data.

The local CSV provider is configured correctly and fails with an actionable message when the required file is absent. No empirical validation metrics were produced, and no sector assumptions should be tuned yet.

## Scope Guardrails

No macro scoring formulas were changed.

No sector scoring assumptions were changed.

No news ingestion, trading logic, allocation logic, portfolio sizing, security recommendations, or ALFRED/vintage backtesting were added.

## Validation Configuration

Configured validation file:

```text
data/sector_proxy_prices.csv
```

CSV status:

```text
not present
local-only path under ignored data/ directory
not committed
```

Expected schema:

```text
ticker,date,close
```

Required tickers:

```text
SPY
XLC
XLY
XLP
XLE
XLF
XLV
XLI
XLK
XLB
XLRE
XLU
```

## Commands Run

Validation commands:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
```

Results:

```text
pytest: 123 passed, 2 skipped
ruff: all checks passed
validate-config: Config valid: 13 sources, 11 dimensions, 6 regimes
ingest-sector-proxy-prices: blocked by missing CSV
```

CLI failure:

```text
sector proxy price CSV not found: data\sector_proxy_prices.csv.
Provide a CSV with ticker,date,close columns or use mocked prices in tests.
```

This is the correct behavior for v0.2-G2 when the real local CSV is unavailable.

## Empirical Validation Metrics

No empirical metrics are available because no local proxy price rows were ingested.

```text
price row count: 0
tickers loaded: none
missing tickers: all required tickers
validation date range: n/a
valid observation count: 0
1-month rank IC: n/a
3-month rank IC: n/a
top-minus-bottom spread: n/a
hit rate top positive: n/a
```

No claim can be made yet about whether higher sector macro scores correspond to better future sector-relative returns.

## Implementation Status

The validation machinery itself remains covered by deterministic mocked tests.

Existing tests prove:

```text
forward return calculation
relative return calculation versus SPY
missing price handling
rank correlation calculation
validation summary calculation
report generation
CLI behavior
Stooq response diagnostics
```

The missing piece is real ETF proxy price data, not validation logic.

## Recommendation

Do not tune sector exposures or regime priors yet.

Do not proceed to v0.2 release hardening yet if empirical proxy validation is required as a gate.

Next step:

```text
Place a local CSV at data/sector_proxy_prices.csv with columns ticker,date,close.
```

Then rerun:

```text
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
python -m macro_engine.cli run-sector-validation --config config/sector_validation.yaml
python -m macro_engine.cli sector-validation-summary
python -m macro_engine.cli write-sector-validation-report --config config/sector_validation.yaml
```

Once the CSV is available, create a follow-up empirical review with actual 1-month and 3-month rank IC, top-minus-bottom spreads, hit rates, and assumption concerns.
