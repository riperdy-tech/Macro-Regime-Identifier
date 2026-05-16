# v0.4-M3 Expanded Real-News Pilot Review

## Verdict

v0.4-M3 passes.

The expanded pilot ran 120 real RSS-derived news items through the hardened live classifier, news scoring, and combined sector diagnostic path. Classification reliability improved materially versus v0.4-M1.

No news scoring formulas, macro formulas, sector assumptions, or combined diagnostic formulas were tuned.

## Pilot Data Source

Expanded pilot input file:

```text
data/news_pilot/news_items_expanded.csv
```

The file is local-only and ignored by git.

The file was built from public Google News RSS search results using macro/sector-oriented queries around:

- U.S. economy, inflation, and Federal Reserve
- labor market and jobless claims
- oil and energy supply disruptions
- credit spreads and bank lending
- real estate and mortgage rates
- consumer spending
- manufacturing
- Treasury yields and yield curve risk
- geopolitical risk and shipping
- commodities and materials

## Input Validation

Command:

```text
python -m macro_engine.cli validate-news-input --config config/news_sources.yaml --profile pilot_expanded_local_csv
```

Validation result:

```text
raw items: 120
unique items: 120
duplicates: 0
sources: 72
date range: 2018-09-23 to 2026-05-16
```

Warnings:

```text
1 item has very short body text
16 items are older than one year
```

The old items came from RSS search results, not ingestion errors. They are acceptable for a pilot but should be filtered in a future production-quality collection workflow.

## Live Classification

AI provider/model:

```text
provider: deepseek
model: deepseek-v4-flash
```

Live AI was used with a local-only config:

```text
enable_schema_repair: true
max_retries: 1
```

Classification result:

```text
total items: 120
successful classifications: 120
failed classifications: 0
success rate: 100.0%
M1 success rate: 80.0%
retry count: 1
retry successes: 1
repaired count: 0
unrepaired failure count: 0
theme score rows: 240
sector impact rows: 190
```

The improvement appears driven mainly by prompt/schema hardening and schema expansion for `region` and `person`. The single retry successfully corrected one response.

## Remaining Failure Modes

No classifications failed in the expanded run.

Remaining risks:

- the pilot sample is still RSS-query shaped and may overrepresent energy/inflation themes
- old RSS search results should be filtered for operational use
- the model may still drift on future providers or unusual article structures
- repair count was zero in this run, so repair behavior remains mostly unit-tested rather than heavily exercised live

## News Score Output

News scoring command:

```text
python -m macro_engine.cli build-news-scores --config data/news_pilot/news_scoring_expanded.yaml --db-path data/news_pilot/news_pilot_expanded.duckdb
```

Result:

```text
daily theme rows: 1564
daily sector rows: 1113
weekly theme rows: 272
weekly sector rows: 188
component rows: 6484
status: success
```

Latest news score date:

```text
2026-05-16
```

Top positive macro themes:

```text
inflation_pressure: 4.2302
monetary_tightening: 2.2337
labor_strength: 1.1097
commodity_pressure: 0.3808
growth_acceleration: 0.3763
```

Top sector news tailwinds:

```text
energy: 1.9207
financials: 0.7782
materials: 0.2174
```

Top sector news headwinds:

```text
consumer_discretionary: -1.7460
real_estate: -0.9843
utilities: -0.7620
industrials: -0.6853
consumer_staples: -0.1347
```

## Combined Diagnostic Output

Combined diagnostic command:

```text
python -m macro_engine.cli build-combined-sector-diagnostics --config data/news_pilot/sector_news_integration_expanded.yaml --db-path data/news_pilot/news_pilot_expanded.duckdb
```

Result:

```text
combined rows: 4796
component rows: 14388
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

Largest sector rank changes from news overlay:

```text
No sector rank changed on the latest date.
```

This is acceptable pilot behavior. News affected sector component scores, but it did not overwhelm the macro-sector ranking.

## Manual Plausibility Audit

Reviewed classification examples included:

### High-confidence energy/geopolitical classification

```text
Title: Hormuz disruption deepens global economic strain across trade, prices and finance
Severity: 0.90
Confidence: 0.80
Assessment: plausible; energy/geopolitical/inflation implications were supported by the text.
```

### High-confidence energy supply shock classification

```text
Title: Strait of Hormuz Crisis: How Markets Have Handled the "Largest Oil Supply Disruption in History" So Far
Severity: 0.90
Confidence: 0.90
Assessment: plausible; sector and macro impacts map cleanly to energy supply shock and inflation pressure.
```

### Labor-market classification

```text
Title: Weekly U.S. jobless claims fall to 189,000, lowest in more than five decades
Severity: 0.90
Confidence: 0.95
Assessment: plausible; labor_strength classification is supported.
```

### Policy/inflation classification

```text
Title: Traders now see next Fed interest rate move as a hike following inflation surge
Severity: 0.80
Confidence: 0.90
Assessment: plausible; monetary_tightening and inflation_pressure are supported.
```

### Repaired classification

```text
No live classification required repair in the expanded pilot.
```

### Retried classification

```text
One classification required retry and succeeded on retry.
```

### Failed classification

```text
No failed classifications in the expanded pilot.
```

## Plausibility Assessment

The expanded output is plausible given the input sample:

- energy was strongly supported by multiple supply-shock/geopolitical items
- inflation pressure and monetary tightening dominated macro news themes
- consumer discretionary and real estate were pressured by rates/inflation-sensitive interpretations
- combined ranking remained stable despite stronger news signals

The main caveat is input-query bias. The RSS query set intentionally targeted macro stress/inflation/energy/rates themes, so the resulting news scores should not be interpreted as a balanced real-time news sample.

## Guardrail Audit

Generated reports checked:

```text
outputs/news_pilot_expanded/news_classification_report.md
outputs/news_pilot_expanded/news_score_report.md
outputs/news_pilot_expanded/combined_sector_diagnostic.md
```

Forbidden-language audit result:

```text
no matches
```

Report display text sanitizes forbidden market-action substrings in raw headlines while preserving raw text in storage.

## Scoring Decision

News scoring formulas should remain unchanged.

The bottleneck improved from schema reliability to data collection quality:

```text
M1 success rate: 80.0%
M3 success rate: 100.0%
```

No calibration should happen until there is a more balanced and time-consistent real-news history.

## Recommended Next Milestone

v0.4-M4 should be:

```text
real-news accumulation and monitoring
```

Priority follow-ups:

- collect a more balanced daily real-news sample
- filter old RSS search results
- track live classification success rate over time
- monitor repair/retry rates
- monitor whether news overlay rank changes remain bounded
- defer scoring calibration until enough real history exists

This pilot is not investment advice, not a trading backtest, and not a portfolio allocation system.
