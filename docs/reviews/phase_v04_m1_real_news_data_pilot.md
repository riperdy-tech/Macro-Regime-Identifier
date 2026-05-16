# v0.4-M1 Real News Data Pilot Review

## Verdict

v0.4-M1 passes as a real-news pilot.

The pilot ingested real RSS-derived news items from a local CSV, ran live DeepSeek classification, aggregated the successful classifications into news scores, and built the combined macro-sector-news diagnostic. The pilot also exposed one report-formatting bug, which was fixed without changing scoring formulas.

This was a data pilot, not a tuning phase. v0.1 macro scoring, v0.2 sector scoring, and v0.3 combined formulas remain unchanged.

## Data Source

Pilot input file:

```text
data/news_pilot/news_items.csv
```

The file is local-only and ignored by git.

The file was built from public Google News RSS search results using macro/sector-oriented queries around:

- U.S. economy, inflation, and Federal Reserve
- labor market and jobless claims
- oil and energy supply disruptions
- credit spreads and financial conditions
- real estate and interest rates

Required schema:

```text
title, body, source, source_url, published_at
```

## Input Validation

Command:

```text
python -m macro_engine.cli validate-news-input --config config/news_sources.yaml --profile pilot_local_csv
```

Validation result:

```text
raw items: 40
unique items: 40
duplicates: 0
date range: 2025-06-29 to 2026-05-16
selected source count: 1
warning: 1 item has very short body text
```

Sources represented: 34 distinct publisher/source labels, including Reuters, CNBC, CNN, MarketWatch, WSJ, Yahoo Finance, World Bank Group, The New York Times, and others.

## Ingestion And Classification

Ingestion command:

```text
python -m macro_engine.cli ingest-news --config config/news_sources.yaml --profile pilot_local_csv --db-path data/news_pilot/news_pilot.duckdb
```

Ingestion result:

```text
news rows: 40
```

Live AI classification command:

```text
python -m macro_engine.cli classify-news --config data/news_pilot/news_ai_live.yaml --db-path data/news_pilot/news_pilot.duckdb
```

AI provider/model:

```text
provider: deepseek
model: deepseek-v4-flash
```

Live AI was used. The API key was read from the local environment and was not committed.

Classification result:

```text
classification rows: 40
successful classifications: 32
failed classifications: 8
theme score rows: 60
sector impact rows: 57
```

## Classification Failures

The failed rows were useful guardrail tests. They were not silent failures.

Observed failure types:

- `direction` values outside the allowed enum, such as `uncertainty` or `uncertain`
- `entity_type` values outside the allowed enum, such as `region` or `person`
- numeric scores outside allowed bounds, such as severity or confidence greater than 1.0

This suggests v0.4-M2 should improve the prompt/schema contract or add a bounded repair step before validation, while still preserving strict storage validation.

## Example Classifications

Examples of successful classifications:

```text
Middle East war energy shock
- summary: global responses to a historic energy shock triggered by war in the Middle East
- severity: 0.90
- confidence: 0.85

Strait of Hormuz oil supply disruption
- summary: significant geopolitical risk and energy supply shock with inflation pressure
- severity: 0.90
- confidence: 0.85

U.S. jobless claims at a 57-year low
- summary: strong labor market with tight conditions
- severity: 0.80
- confidence: 0.80

Fed inflation risk headline
- summary: persistent inflation pressure and policy risk
- severity: 0.80
- confidence: 0.90

Energy shock and gas price caps
- summary: persistent supply constraints and possible regulatory response
- severity: 0.80
- confidence: 0.70
```

## News Score Output

News scoring command:

```text
python -m macro_engine.cli build-news-scores --config data/news_pilot/news_scoring_pilot.yaml --db-path data/news_pilot/news_pilot.duckdb
```

Result:

```text
daily theme rows: 319
daily sector rows: 241
weekly theme rows: 55
weekly sector rows: 43
component rows: 1556
status: success
```

Latest news score date:

```text
2026-05-16
```

Top macro themes:

```text
inflation_pressure: 1.9066
monetary_tightening: 0.8857
labor_strength: 0.7526
energy_supply_shock: 0.5968
geopolitical_risk: 0.5669
```

Top sector news tailwinds:

```text
energy: 1.3879
financials: 0.6738
materials: 0.3170
real_estate: 0.3000
utilities: 0.2000
```

Top sector news headwinds:

```text
consumer_discretionary: -0.3156
industrials: -0.3142
```

The output is plausible for the collected pilot set, which contained a noticeable cluster of inflation, rates, labor, energy, and geopolitical-risk items.

## Combined Diagnostic Output

Combined diagnostic command:

```text
python -m macro_engine.cli build-combined-sector-diagnostics --config data/news_pilot/sector_news_integration_pilot.yaml --db-path data/news_pilot/news_pilot.duckdb
```

Result:

```text
combined rows: 847
component rows: 2541
```

Latest combined diagnostic date:

```text
2026-05-16
```

Combined ranking:

```text
1. energy
2. materials
3. financials
4. industrials
5. consumer_staples
6. health_care
7. consumer_discretionary
8. communication_services
9. information_technology
10. real_estate
11. utilities
```

Largest rank changes caused by the news overlay:

```text
No sector rank changed on the latest date.
```

This is a healthy first result: the news overlay affected component scores but did not overwhelm the macro-sector ranking.

## Plausibility Audit

The pilot behavior is directionally plausible:

- energy received positive news support from energy supply shock and inflation-related items
- financials received positive news support in the current sample, likely from rate/financial-condition interpretations
- consumer discretionary and industrials received negative news pressure from inflation/rates/growth-sensitive items
- the combined layer remained stable and did not reorder sectors on the latest date

No formula changes are recommended yet. The next step should be more real data and prompt/schema hardening, not scoring calibration.

## Clearly Wrong Or Suspicious Classifications

No stored successful classification obviously used invalid sector IDs or invalid theme IDs, because invalid outputs were rejected before storage.

However, 8 of 40 live responses failed schema validation. That is the main issue found by this pilot. The failures were mostly enum drift and score scaling drift, not storage or aggregation bugs.

Potential v0.4-M2 improvements:

- tighten prompt wording around allowed enum values
- add explicit "scores must be decimals from 0.0 to 1.0" examples
- optionally add a safe repair/normalization pass for harmless enum aliases
- keep failed rows visible and auditable

## Bug Found And Fixed

The live pilot exposed a report formatting bug:

```text
failed classifications can have null confidence
```

The Markdown classification report previously assumed confidence was numeric. This was fixed by formatting null scores as `n/a`.

The pilot also exposed real headline language that contained a forbidden market-action substring. Markdown report display text now sanitizes forbidden terms in item titles/summaries while preserving raw text in storage.

These were report/guardrail fixes only. No scoring formulas changed.

## Guardrail Audit

Pilot reports checked:

```text
outputs/news_pilot/news_classification_report.md
outputs/news_pilot/news_score_report.md
outputs/news_pilot/combined_sector_diagnostic.md
```

Forbidden-language audit result:

```text
no matches
```

The reports remain diagnostic-only and do not introduce market-action language.

## Repository Hygiene

Local-only ignored paths:

```text
data/news_pilot/
outputs/news_pilot/
data/news_pilot/news_pilot.duckdb
data/news_pilot/news_ai_live.yaml
```

`.env`, API keys, local data, DuckDB files, outputs, and caches were not staged.

`data/news_pilot/` was added to `.gitignore` so real pilot files remain local-only by default.

## Validation

Validation commands run during the milestone:

```text
python -m pytest
python -m ruff check .
python -m macro_engine.cli validate-config
python -m macro_engine.cli validate-news-input --config config/news_sources.yaml --profile pilot_local_csv
```

Final validation result:

```text
pytest: 140 passed, 2 skipped
ruff: clean
validate-config: passed
pilot input validation: passed with one short-body warning
pilot report guardrail audit: no forbidden-language matches
```

## Decision

v0.4-M1 passes.

Recommendation for v0.4-M2:

```text
prompt/schema/source-quality improvement
```

Do not tune news scoring yet. First improve live classification success rate and continue accumulating real news history.

This pilot is not investment advice, not a trading backtest, and not a portfolio allocation system.
