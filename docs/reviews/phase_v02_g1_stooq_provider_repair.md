# v0.2-G1 Stooq Provider Repair

Date: 2026-05-14

## Verdict

v0.2-G1 passes as a provider repair.

The prior conclusion that Stooq simply requires a normal API key was too strong. Public references still describe Stooq as a CSV/download-oriented data source rather than a conventional API service. The repaired provider now verifies Stooq response shape directly and fails with a clear diagnostic when the response is not CSV.

The current live result in this environment:

```text
Stooq endpoint reached: yes
CSV data received: no
price rows ingested: 0
failure mode: apikey_instruction response
```

This means v0.2-G sector validation should not proceed with Stooq live data yet unless a usable CSV download path is available. The local CSV provider remains the reliable fallback.

## Sources Checked

Reference behavior:

```text
pandas-datareader StooqDailyReader URL: https://stooq.com/q/d/l/
pandas-datareader parameter pattern: s=<symbol>, i=d, d1=YYYYMMDD, d2=YYYYMMDD
pandas-datareader U.S. suffix behavior: symbols without suffix receive .US
QuantStart description: Stooq data is downloadable CSV/data-file oriented and "there is no API for Stooq"
```

Relevant URLs:

```text
https://github.com/pydata/pandas-datareader/blob/main/pandas_datareader/stooq.py
https://www.quantstart.com/articles/an-introduction-to-stooq-pricing-data/
```

## Endpoint Tested

The repaired provider uses:

```text
https://stooq.com/q/d/l/?s=spy.us&i=d&d1=19981222&d2=20260514
```

Ticker normalization:

```text
SPY  -> spy.us
XLE  -> xle.us
XLF  -> xlf.us
XLK  -> xlk.us
XLU  -> xlu.us
XLI  -> xli.us
XLP  -> xlp.us
XLY  -> xly.us
XLV  -> xlv.us
XLB  -> xlb.us
XLRE -> xlre.us
XLC  -> xlc.us
```

The provider also:

```text
uses a requests.Session
sets User-Agent, Accept, and Referer headers
makes an initial https://stooq.com/ request before CSV requests
inspects content type
captures the first 200 response characters
classifies responses as csv, html, empty, apikey_instruction, csv_parse_error, missing_csv_columns, or non_csv
```

## Live Response Diagnostic

Command run:

```text
python -m macro_engine.cli ingest-sector-proxy-prices --config config/sector_validation.yaml
```

Result:

```text
exit: clear failure
classification: apikey_instruction
status_code: 200
content_type: text/plain; charset=UTF-8
stooq_symbol: spy.us
url: https://stooq.com/q/d/l/?s=spy.us&i=d&d1=19981222&d2=20260514
preview: Get your apikey: 1. Open https://stooq.com/q/d/?s=spy.us&get_apikey ...
```

Interpretation:

```text
The endpoint, ticker suffix, session, cookies, and headers are not enough to retrieve CSV in this environment.
The provider is receiving a plain-text instruction page rather than CSV.
The provider now reports that precisely instead of silently returning zero rows or mislabeling the problem.
```

## Code Changes

Changed:

```text
src/macro_engine/sectors/validation.py
tests/test_phase_v02_f_sector_validation.py
config/sector_validation.yaml
.env.example
docs/reviews/phase_v02_f_sector_etf_proxy_validation.md
```

Behavior changes:

```text
default sector_validation.yaml no longer advertises STOOQ_API_KEY
.env.example no longer includes STOOQ_API_KEY
Stooq ticker normalization is explicit
Stooq response classification is explicit
non-CSV Stooq responses produce a clear CLI error
CSV local-file provider remains unchanged
optional api_key_env support remains in code only as an escape hatch if a future config explicitly supplies it
```

## Tests Added

Added/verified tests for:

```text
ticker normalization to Stooq symbols
CSV response parsing
HTML response handling
empty response handling
apikey-instruction response handling
```

Focused results:

```text
tests/test_phase_v02_f_sector_validation.py: 10 passed
ruff: all checks passed
```

## Recommendation

Do not tune sector assumptions based on Stooq live validation yet.

Do not proceed to empirical sector validation using this Stooq provider until one of these is true:

```text
1. A local CSV of ETF proxy prices is supplied.
2. A verified Stooq CSV download path returns actual CSV data.
3. Another proxy price provider is added behind the same pluggable interface.
```

For the next phase, prefer one of:

```text
v0.2-G2: local CSV proxy price validation using supplied ETF history
v0.2-G3: alternate provider adapter for ETF proxy prices
```

The sector mapper itself remains intact and unchanged.
