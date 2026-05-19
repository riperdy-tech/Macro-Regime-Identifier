# v0.6-M1 Real News Source Expansion

## Verdict

v0.6-M1 passes as source coverage infrastructure.

This milestone added practical source-profile and watchlist support for broader
real-news collection without changing macro, sector, news, or combined scoring
formulas.

## What Changed

Added source profiles in `config/news_sources.yaml`:

```text
daily_local_csv
daily_local_json
rss_watchlist
```

Added:

```text
config/news_source_watchlist.yaml
src/macro_engine/news/source_coverage.py
```

Added CLI commands:

```text
python -m macro_engine.cli validate-news-sources --config config/news_source_watchlist.yaml
python -m macro_engine.cli news-source-coverage --config config/news_source_watchlist.yaml
python -m macro_engine.cli write-news-source-coverage-report --config config/news_source_watchlist.yaml
```

The source watchlist covers the required groups:

```text
macro_general
inflation_rates
labor
energy_commodities
credit_financial_conditions
real_estate
consumer
manufacturing_industrials
geopolitical
technology_ai
healthcare
defensive_sectors
```

## RSS Support

A simple RSS provider was added to the local news ingestion layer. It supports:

```text
feed_url
source
source_group
max_items
lookback_days
title/body extraction
source_url
published_at
```

RSS sources fail clearly if a feed is unreachable or returns non-XML content.
Tests use mocked feed data and do not make network calls.

## Coverage Report

The coverage report writes:

```text
outputs/news_source_coverage_report.json
outputs/news_source_coverage_report.md
```

Latest source coverage command result:

```text
configured_source_count: 14
enabled_source_count: 13
configured_groups: 12
stored_item_count: 6
```

Current source coverage remains thin because the stored release data is the
synthetic/mock sample. The report maps those six stored items to
`macro_general`, flags the other configured groups as missing stored items, and
marks the sample data as stale. That is the correct behavior for the current
workspace.

## Tests

Focused v0.6 tests passed:

```text
python -m pytest tests/test_phase_v06_m1_m2_operations.py
5 passed
```

Full suite passed:

```text
python -m pytest
158 passed, 2 skipped
```

Ruff passed:

```text
python -m ruff check .
All checks passed
```

Config validation passed:

```text
python -m macro_engine.cli validate-config
Config valid: 13 sources, 11 dimensions, 6 regimes
```

## Known Limitations

The watchlist config defines balanced coverage targets, but it does not fetch or
guarantee balanced real data by itself. Real daily files or enabled RSS feeds
must be maintained over time.

The disabled RSS placeholder demonstrates configuration shape only. It is not a
paid feed integration and does not introduce a paid API dependency.

## M2 Readiness

Scheduled daily run support can proceed.

No scoring formulas were changed. No market-action language was introduced.
